#!/usr/bin/env bash
# 启动麻薯电脑秘书：tmux 会话 mashu-studio（窗口 office = 网页服务与调度器；desk = 麻薯主脑；film-<id> = 各项目的子脑与执行者）
#   ./start.sh          启动（已在运行则只检查）并打开网页
#   ./start.sh stop     停止整个 tmux 会话（包括所有会话）
#   ./start.sh restart  只重启网页服务（改了 Python 代码后用；各会话不受影响）
#   ./start.sh autostart on|off   开机（登录后）自动启动，并每分钟检查一次，网页服务挂了自动拉起
#   ./start.sh auto     给 launchd 用：手动 stop 过就不拉起；网页服务没响应就重启它
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SESSION="${MASHU_TMUX:-mashu-studio}"
PORT="${MASHU_PORT:-8770}"
URL="http://127.0.0.1:${PORT}/"
STOPPED="$ROOT/state/.stopped"   # 手动 stop 留下的标记，自动启动看到它就不拉起
LABEL="${MASHU_LAUNCHD_LABEL:-com.mashu-studio}"   # launchd 标签；旧版装过的自动启动沿用旧标签
for _old in "$HOME"/Library/LaunchAgents/com.*.mashu-studio.plist; do
  [[ -f "$_old" ]] && LABEL="$(basename "$_old" .plist)" && break
done
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SERVER_CMD="cd '$ROOT' && python3 mashu/server.py --port $PORT; echo '服务已退出，按回车关闭窗口'; read -r _"

MODE="${1:-}"
case "$MODE" in
  stop)
    mkdir -p "$ROOT/state" && touch "$STOPPED"
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "已停止 tmux 会话 $SESSION" || echo "$SESSION 未在运行"
    exit 0 ;;
  restart)
    # 按窗口 id 逐个关：同名 office 窗口有两个时（两次启动撞车留下的），按名字找会报「找不到窗口」，重启就静默失效
    for _w in $(tmux list-windows -t "$SESSION" -F '#{window_id} #{window_name}' 2>/dev/null | awk '$2=="office"{print $1}'); do
      tmux kill-window -t "$_w" 2>/dev/null || true
    done ;;
  auto)
    [[ -e "$STOPPED" ]] && exit 0
    NO_OPEN=1
    # office 窗口还在但服务没响应（进程退出后窗口停在“按回车关闭”）：重启网页服务
    if tmux list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -qx office \
       && ! curl -fsS -m 5 -o /dev/null "$URL" 2>/dev/null; then
      sleep 3
      curl -fsS -m 5 -o /dev/null "$URL" 2>/dev/null || { echo "$(date '+%F %T') 网页服务无响应，重启"; for _w in $(tmux list-windows -t "$SESSION" -F '#{window_id} #{window_name}' | awk '$2=="office"{print $1}'); do tmux kill-window -t "$_w"; done; }
    fi ;;
  autostart)
    case "${2:-}" in
      on)
        mkdir -p "$ROOT/state" "$(dirname "$PLIST")"
        cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$ROOT/start.sh</string>
        <string>auto</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PATH</string>
        <key>LANG</key>
        <string>${LANG:-en_US.UTF-8}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>$ROOT/state/autostart.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/state/autostart.log</string>
</dict>
</plist>
EOF
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        echo "已开启自动启动：${PLIST}（PATH 取自当前终端，换了工具链路径就重新执行一次）" ;;
      off)
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST"
        echo "已关闭自动启动（正在运行的会话不受影响）" ;;
      *)
        launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && echo "自动启动：开" || echo "自动启动：关"
        echo "用法：./start.sh autostart on|off" ;;
    esac
    exit 0 ;;
esac
[[ "$MODE" == auto ]] || rm -f "$STOPPED"

command -v tmux >/dev/null || { echo "需要 tmux（brew install tmux）"; exit 1; }
command -v claude >/dev/null || { echo "需要 claude CLI"; exit 1; }

STARTED=
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -n office -c "$ROOT" "$SERVER_CMD"; STARTED=1
elif ! tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx office; then
  tmux new-window -d -t "$SESSION:" -n office -c "$ROOT" "$SERVER_CMD"; STARTED=1
fi

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "$URL" 2>/dev/null && break
  sleep 0.5
done
curl -fsS -o /dev/null "$URL" || { echo "网页服务没起来，查看：tmux attach -t ${SESSION}（office 窗口）"; exit 1; }

# 微信连接随麻薯会话运行；桥接进程退出后在同一窗口中重试。
if [[ -f "$HOME/.mashu-weixin/account.json" ]] && \
   ! tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx weixin; then
  WEIXIN_CMD="cd '$ROOT'; umask 077; while true; do MASHU_PORT='$PORT' MASHU_HOME='${MASHU_HOME:-$ROOT/state}' python3 -u integrations/weixin/bridge.py >> '$HOME/.mashu-weixin/bridge.log' 2>&1; sleep 5; done"
  tmux new-window -d -t "$SESSION:" -n weixin -c "$ROOT" "$WEIXIN_CMD"
fi

if [[ "$MODE" == auto ]]; then
  [[ -n "$STARTED" ]] && echo "$(date '+%F %T') 已自动启动：$URL"
  exit 0
fi
echo "麻薯电脑秘书已启动：$URL"
echo "看所有会话：tmux attach -t $SESSION   （Ctrl-b w 选窗口，Ctrl-b z 放大窗格，Ctrl-b d 退出不停止）"
[[ "${NO_OPEN:-}" == "1" ]] || open "$URL" 2>/dev/null || true
