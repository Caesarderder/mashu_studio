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
// 实时动态（这一轮的工具列表、状态与耗时、思考片段、待办、子代理、已写出的文字）原子写到 $MASHU_HOME/live/<机位>.json，网页读它。
import { spawn, spawnSync } from "node:child_process"
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { join } from "node:path"

const CID = process.env.MASHU_SESSION || ""
const BIN = process.env.MASHU_BIN || "mashu"
const HOME = process.env.MASHU_HOME || ""
const LIVE = HOME && CID ? join(HOME, "live", `${CID}.json`) : ""

const TOOL_NAMES = {
  bash: "Bash", edit: "Edit", write: "Write", multiedit: "MultiEdit",
  read: "Read", grep: "Grep", glob: "Glob", list: "LS",
  task: "Task", apply_patch: "Edit", patch: "Edit",
  todowrite: "TodoWrite", todoread: "TodoRead",
  webfetch: "WebFetch", websearch: "WebSearch",
  skill: "Skill", question: "Question", lsp: "LSP",
}

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

function firstLine(s, max = 60) {
  const line = String(s || "").trim().split("\n")[0].trim()
  return line.slice(0, max)
}

function patchFirstFile(text) {
  const m = /^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$/m.exec(String(text || ""))
  return m ? m[1].trim().split("/").pop() : ""
}

function toolMeta(part) {
  const s = part.state || {}
  const inp = s.input || {}
  const rawTool = String(part.tool || "工具")
  const name = TOOL_NAMES[rawTool.toLowerCase()] || rawTool
  let detail = ""
  if (rawTool === "apply_patch" || rawTool === "patch") {
    detail = patchFirstFile(inp.patchText || inp.patch || inp.input)
  } else if (inp.filePath || inp.file_path || inp.path) {
    const fp = String(inp.filePath || inp.file_path || inp.path).trim()
    detail = fp.split("/").pop() || fp
  } else {
    detail = inp.command || inp.pattern || inp.url || inp.description || inp.query || inp.prompt || ""
  }
  return { name, detail: firstLine(detail, 60) }
}

function formatLabel(name, detail) {
  return detail ? `${name} · ${detail}` : name
}

function toSec(v) {
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return null
  return v > 1e12 ? v / 1000 : v
}

function mapToolState(st) {
  if (st === "pending" || st === "running") return "doing"
  if (st === "error") return "error"
  return "done"
}

function normalizeTodos(list) {
  if (!Array.isArray(list)) return null
  const out = []
  for (const x of list) {
    if (!x || typeof x !== "object") continue
    const text = String(x.content || x.subject || x.text || "").trim()
    if (!text) continue
    const st = String(x.status || x.state || "pending").toLowerCase()
    out.push({ text, state: st === "completed" || st === "done" ? "completed" : (st === "in_progress" ? "in_progress" : "pending") })
  }
  return out
}

export const MashuPlugin = async () => {
  if (!CID) return {}
  let system = null
  const parent = new Map()        // 会话 → 父会话（子代理的会话不算这一轮主文字）
  const role = new Map()          // 消息 → user / assistant
  const texts = new Map()         // 这一轮助手消息的文字：part id → { msg, text }
  const tools = new Map()         // 这一轮全部工具调用：callID → { id, name, detail, state, start, end }
  const agents = new Map()        // 子代理（task 工具或子会话）：key → { id, kind, tool, type, desc, model, state, start, end, bg, childSid }
  const childToAgent = new Map()  // 子会话 ID → agents key
  const msgOut = new Map()        // 这一轮助手消息输出 token：msg id → number
  let todos = []                  // 最新待办列表
  let thinking = ""               // 最新思考片段
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
  const turnOut = () => {
    let sum = 0
    for (const n of msgOut.values()) sum += n
    return sum
  }
  const activeTool = () => {
    let last = ""
    for (const t of tools.values()) {
      if (t.state === "doing") last = formatLabel(t.name, t.detail)
    }
    return last
  }

  const flushLive = () => {
    if (!LIVE) return
    if (writeTimer) { clearTimeout(writeTimer); writeTimer = null }
    try {
      mkdirSync(join(HOME, "live"), { recursive: true })
      const tmp = LIVE + ".tmp"
      const allTools = [...tools.values()]
      const agentList = [...agents.values()].map(({ childSid, ...rest }) => rest).slice(-12)
      const payload = {
        sid: root,
        text: allText(),
        last_reply: lastReply(),
        tool: activeTool(),
        tools: allTools.slice(-40),
        tools_total: allTools.length,
        thinking: thinking.slice(-1500),
        todos,
        agents: agentList,
        shells: [],
        turn_start: turnStart || null,
        turn_out: turnOut(),
        model: model || undefined,
        at: Date.now() / 1000,
      }
      writeFileSync(tmp, JSON.stringify(payload))
      renameSync(tmp, LIVE)
    } catch (e) {}
  }

  const writeLive = (immediate = false) => {
    if (!LIVE) return
    if (immediate) return flushLive()
    if (writeTimer) return
    writeTimer = setTimeout(flushLive, 250)
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
      texts.clear(); tools.clear(); msgOut.clear()
      thinking = ""
      turnStart = Date.now() / 1000
      // 保留还在跑的后台子代理，清理很久之前已结束的
      const now = turnStart
      for (const [k, a] of agents.entries()) {
        if (a.state !== "running" && a.end && now - a.end > 600) agents.delete(k)
      }
      const prompt = (output.parts || []).filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join("\n")
      const extra = call("hook-prompt", { session_id: sid, prompt, first }, 20000)
      first = false
      if (extra && output.message) {
        output.parts.push({ id: `prt_mashu${Date.now()}`, sessionID: sid, messageID: output.message.id, type: "text", text: extra, synthetic: true })
      }
      writeLive(true)
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
          } else if (info.id && root && info.parentID === root) {
            // 挂在主会话下的子代理会话：若尚无对应 task 条目则登记
            const existingKey = childToAgent.get(info.id)
            if (!existingKey) {
              const key = `ses-${info.id}`
              childToAgent.set(info.id, key)
              agents.set(key, {
                id: info.id, kind: "agent", tool: "", type: info.agent || "subagent",
                desc: firstLine(info.title || "", 80),
                model: info.model ? `${info.model.providerID}/${info.model.id}` : "",
                state: "running", start: toSec(info.time && info.time.created) || Date.now() / 1000,
                end: null, bg: true, childSid: info.id,
              })
              writeLive()
            }
          }
          break
        }
        case "session.compacted":
          if (isRoot(p.sessionID)) first = true
          break
        case "todo.updated":
          if (isRoot(p.sessionID) && Array.isArray(p.todos)) {
            const norm = normalizeTodos(p.todos)
            if (norm) { todos = norm; writeLive() }
          }
          break
        case "message.updated": {
          const info = p.info || {}
          role.set(info.id, info.role)
          if (info.role === "assistant" && isRoot(info.sessionID)) {
            if (info.providerID && info.modelID) model = `${info.providerID}/${info.modelID}`
            if (info.tokens) {
              const outTok = (info.tokens.output || 0) + (info.tokens.reasoning || 0)
              if (outTok > 0) { msgOut.set(info.id, outTok); writeLive() }
            }
            if (info.time && info.time.completed) {
              fire("statusline", { session_id: info.sessionID, model, variant, last: info.tokens || {}, cost: info.cost })
            }
          }
          break
        }
        case "message.part.updated": {
          const part = p.part || {}
          // 子代理会话的工具动态：更新对应子代理的当前子工具
          const childKey = childToAgent.get(part.sessionID)
          if (childKey && agents.has(childKey) && part.type === "tool") {
            const ag = agents.get(childKey)
            const tm = toolMeta(part)
            ag.tool = formatLabel(tm.name, tm.detail)
            writeLive()
            break
          }
          if (!isRoot(part.sessionID) || role.get(part.messageID) === "user") break
          if (part.type === "text" && !part.synthetic) {
            texts.set(part.id, { msg: part.messageID, text: part.text || "" })
            writeLive()
          } else if (part.type === "reasoning") {
            const rt = String(part.text || "").trim()
            if (rt) { thinking = rt; writeLive() }
          } else if (part.type === "step-finish" && part.tokens) {
            const outTok = (part.tokens.output || 0) + (part.tokens.reasoning || 0)
            if (outTok > 0 && part.messageID) {
              msgOut.set(part.messageID, Math.max(msgOut.get(part.messageID) || 0, outTok))
              writeLive()
            }
          } else if (part.type === "tool") {
            const s = part.state || {}
            const tm = toolMeta(part)
            const st = mapToolState(s.status)
            const callID = part.callID || part.id || `t-${tools.size}`
            const prev = tools.get(callID)
            const nowSec = Date.now() / 1000
            const start = toSec(s.time && s.time.start) || (prev && prev.start) || nowSec
            const end = st === "doing" ? null : (toSec(s.time && s.time.end) || (prev && prev.end) || nowSec)
            tools.set(callID, { id: callID, name: tm.name, detail: tm.detail, state: st, start, end })

            const rawTool = String(part.tool || "").toLowerCase()
            const inp = s.input || {}
            if (rawTool === "todowrite" && Array.isArray(inp.todos)) {
              const norm = normalizeTodos(inp.todos)
              if (norm) todos = norm
            } else if (rawTool === "task") {
              const meta = s.metadata || {}
              const childSid = meta.sessionId || meta.sessionID || ""
              const agModel = meta.model && meta.model.modelID ? `${meta.model.providerID || ""}/${meta.model.modelID}`.replace(/^\//, "") : ""
              const agState = st === "done" ? "completed" : (st === "error" ? "failed" : "running")
              // 如果之前先收到 session.created 建了 ses-<childSid>，合并过来避免重复
              if (childSid && childToAgent.get(childSid) === `ses-${childSid}`) {
                agents.delete(`ses-${childSid}`)
              }
              const prevAg = agents.get(callID)
              agents.set(callID, {
                id: childSid || callID, kind: "agent",
                tool: (prevAg && prevAg.tool) || "",
                type: inp.subagent_type || "subagent",
                desc: firstLine(inp.description || inp.prompt || "", 80),
                model: agModel || (prevAg && prevAg.model) || "",
                state: agState, start, end, bg: !!inp.run_in_background, childSid,
              })
              if (childSid) childToAgent.set(childSid, callID)
            }
            writeLive()
          }
          break
        }
        case "session.error":
          if (isRoot(p.sessionID)) fire("hook-notify", { message: "error: " + JSON.stringify(p.error || {}).slice(0, 300) })
          break
        case "session.idle": {
          const sid = p.sessionID
          const childKey = childToAgent.get(sid)
          if (childKey && agents.has(childKey)) {
            const ag = agents.get(childKey)
            if (ag.state === "running") {
              ag.state = "completed"
              ag.end = Date.now() / 1000
              writeLive()
            }
            break
          }
          if (isRoot(sid)) {
            const nowSec = Date.now() / 1000
            for (const t of tools.values()) {
              if (t.state === "doing") { t.state = "done"; t.end = t.end || nowSec }
            }
            writeLive(true)
            fire("hook-stop", { session_id: sid, text: lastReply(), model, variant, total, cost })
          }
          break
        }
      }
    },
  }
}
