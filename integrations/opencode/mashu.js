// 麻薯电脑秘书的 OpenCode 插件：只在麻薯启动的 OpenCode 会话里加载（launch 设 OPENCODE_CONFIG_DIR 指向 state/opencode-config，
// 那里的 plugins/mashu.js 软链接到本文件），用户自己开的 OpenCode 不加载、用户的 ~/.config/opencode 不改。
//
// 对应 Claude 的 hooks：
//   tool.execute.before        → mashu hook-pretool --opencode（同步等结果，拒绝就抛错拦下这次工具调用）
//   chat.message               → mashu hook-prompt --opencode（新会话 / 压缩后的第一轮带上下文恢复；「继续」带状态摘要）
//   experimental.chat.system.transform → 追加麻薯的系统提示（MASHU_SYSTEM_FILE）
//   session.idle（主会话）      → mashu hook-stop --opencode（这一轮最后的回复、累计用量）
//   助手消息完成               → mashu statusline --opencode（上下文、模型、思考档位）
//   shell.env                  → bash 工具里还原用户的 XDG_STATE_HOME（麻薯为隔离 TUI 状态换掉了它）
// 实时动态（这一轮已写出的文字、正在用的工具）写到 $MASHU_HOME/live/<机位>.json，网页读它。
import { spawn, spawnSync } from "node:child_process"
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { join } from "node:path"

const CID = process.env.MASHU_SESSION || ""
const BIN = process.env.MASHU_BIN || "mashu"
const HOME = process.env.MASHU_HOME || ""
const LIVE = HOME && CID ? join(HOME, "live", `${CID}.json`) : ""

function call(cmd, payload, timeout) {
  try {
    const r = spawnSync(BIN, [cmd, "--opencode"], { input: JSON.stringify(payload), encoding: "utf8", timeout, env: process.env })
    return r.status === 0 ? (r.stdout || "").trim() : ""
  } catch (e) {
    return ""
  }
}

function fire(cmd, payload) {
  try {
    const p = spawn(BIN, [cmd, "--opencode"], { stdio: ["pipe", "ignore", "ignore"], env: process.env, detached: false })
    p.on("error", () => {})
    p.stdin.on("error", () => {})
    p.stdin.end(JSON.stringify(payload))
  } catch (e) {}
}

function label(part) {
  const s = part.state || {}
  const inp = s.input || {}
  let d = inp.command || inp.filePath || inp.pattern || inp.url || inp.description || inp.query || ""
  d = String(d).trim().split("\n")[0]
  if (inp.filePath) d = d.split("/").pop()
  const name = { bash: "Bash", edit: "Edit", write: "Write", read: "Read", grep: "Grep", glob: "Glob", task: "Task", apply_patch: "Edit", patch: "Edit" }[part.tool] || part.tool || "工具"
  return d ? `${name} · ${d.slice(0, 60)}` : name
}

export const MashuPlugin = async () => {
  if (!CID) return {}
  let system = null
  const parent = new Map()        // 会话 → 父会话（子代理的会话不算这一轮）
  const role = new Map()          // 消息 → user / assistant
  const texts = new Map()         // 这一轮助手消息的文字：part id → { msg, text }
  const tools = new Map()         // 正在跑的工具：callID → 标签
  let root = ""                   // 主会话（第一条 chat.message 的会话）
  let first = true                // 本进程第一轮 / 压缩之后：要带上下文恢复
  let turnStart = 0
  let model = "", variant = ""
  let total = null, cost = null   // 主会话累计用量（session.updated 给）
  let writeTimer = null

  const isRoot = (sid) => sid && !parent.get(sid) && (!root || sid === root)
  const lastReply = () => {
    let msg = "", out = []
    for (const v of texts.values()) {
      if (v.msg !== msg) { msg = v.msg; out = [] }
      if (v.text && v.text.trim()) out.push(v.text)
    }
    return out.join("\n\n").trim()
  }
  const allText = () => [...texts.values()].map((v) => v.text || "").filter((t) => t.trim()).join("\n\n").trim()
  const writeLive = () => {
    if (!LIVE || writeTimer) return
    writeTimer = setTimeout(() => {
      writeTimer = null
      try {
        mkdirSync(join(HOME, "live"), { recursive: true })
        const tmp = LIVE + ".tmp"
        writeFileSync(tmp, JSON.stringify({ sid: root, text: allText(), tool: [...tools.values()].pop() || "", turn_start: turnStart, at: Date.now() / 1000 }))
        renameSync(tmp, LIVE)
      } catch (e) {}
    }, 300)
  }

  return {
    "experimental.chat.system.transform": async (input, output) => {
      if (system === null) {
        try { system = process.env.MASHU_SYSTEM_FILE ? readFileSync(process.env.MASHU_SYSTEM_FILE, "utf8") : "" } catch (e) { system = "" }
      }
      if (system && input && (!input.sessionID || isRoot(input.sessionID))) output.system.push(system)
    },

    "chat.message": async (input, output) => {
      const sid = input.sessionID
      if (parent.get(sid)) return
      if (!root) root = sid
      if (sid !== root) return
      role.set(output.message && output.message.id, "user")
      texts.clear(); tools.clear(); turnStart = Date.now() / 1000
      const prompt = (output.parts || []).filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join("\n")
      const extra = call("hook-prompt", { session_id: sid, prompt, first }, 20000)
      first = false
      if (extra && output.message) {
        output.parts.push({ id: `prt_mashu${Date.now()}`, sessionID: sid, messageID: output.message.id, type: "text", text: extra, synthetic: true })
      }
      writeLive()
    },

    // 麻薯给 OpenCode 换了 XDG_STATE_HOME（隔离 TUI 状态）：执行者跑的命令还用用户原来的
    "shell.env": async (input, output) => {
      if (process.env.MASHU_USER_XDG_STATE_HOME && output && output.env) output.env.XDG_STATE_HOME = process.env.MASHU_USER_XDG_STATE_HOME
    },

    "tool.execute.before": async (input, output) => {
      const out = call("hook-pretool", { session_id: input.sessionID, tool: input.tool, args: output.args, root: isRoot(input.sessionID) }, 15000)
      if (!out) return
      let d = null
      try { d = JSON.parse(out) } catch (e) { return }
      if (d && d.decision === "deny") throw new Error(d.reason || "麻薯安全拦截")
    },

    event: async ({ event }) => {
      const p = event.properties || {}
      switch (event.type) {
        case "session.created":
        case "session.updated": {
          const info = p.info || {}
          if (info.id) parent.set(info.id, info.parentID || "")
          if (info.id && info.id === root) {
            if (info.model) { model = `${info.model.providerID}/${info.model.id}`; variant = info.model.variant || "" }
            if (info.tokens) total = info.tokens
            if (typeof info.cost === "number") cost = info.cost
          }
          break
        }
        case "session.compacted":
          if (isRoot(p.sessionID)) first = true
          break
        case "message.updated": {
          const info = p.info || {}
          role.set(info.id, info.role)
          if (info.role === "assistant" && isRoot(info.sessionID) && info.time && info.time.completed) {
            model = info.providerID && info.modelID ? `${info.providerID}/${info.modelID}` : model
            fire("statusline", { session_id: info.sessionID, model, variant, last: info.tokens || {}, cost: info.cost })
          }
          break
        }
        case "message.part.updated": {
          const part = p.part || {}
          if (!isRoot(part.sessionID) || role.get(part.messageID) === "user") break
          if (part.type === "text" && !part.synthetic) {
            texts.set(part.id, { msg: part.messageID, text: part.text || "" })
            writeLive()
          } else if (part.type === "tool") {
            const st = (part.state || {}).status
            if (st === "pending" || st === "running") tools.set(part.callID, label(part))
            else tools.delete(part.callID)
            writeLive()
          }
          break
        }
        case "session.error":
          if (isRoot(p.sessionID)) fire("hook-notify", { message: "error: " + JSON.stringify(p.error || {}).slice(0, 300) })
          break
        case "session.idle":
          if (isRoot(p.sessionID)) {
            tools.clear()
            writeLive()
            fire("hook-stop", { session_id: p.sessionID, text: lastReply(), model, variant, total, cost })
          }
          break
      }
    },
  }
}
