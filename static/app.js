/* crawler-agent 前端 —— 消费 POST /agent/run 的 SSE 事件流。
 *
 * 契约见 docs/sse-events.md：改了后端事件类型，必须同步改这里的 handle()。
 *
 * 为什么不用 EventSource：
 *   EventSource 只支持 GET，发不了 POST 的 JSON body。我们这里语义是
 *   「提交一个任务」（有副作用），不能塞进 URL query。
 *
 * 骨架来自 docs/sse-events.md 的客户端示例，加了渲染与错误处理。
 */

const $ = (id) => document.getElementById(id);

const els = {
  q: $("q"),
  go: $("go"),
  status: $("status"),
  events: $("events"),
  answer: $("answer"),
};

let threadId = null;

/* ============================ 渲染层 ============================ */

function setStatus(text, running = false) {
  els.status.textContent = text;
  els.status.classList.toggle("running", running);
}

function clearView() {
  els.events.innerHTML = "";
  els.answer.textContent = "";
  threadId = null;
}

/**
 * 往事件流区追加一行。
 * @param {string} tag    左边那个灰标签，如 "step 2"
 * @param {string} detail 右边的内容
 * @param {string} kind   "" | "ok" | "err"
 *
 * 用 createElement + textContent 而不是拼 innerHTML：
 * 事件内容来自模型和网页，里面可能带 < > 甚至 <script>，
 * 拼字符串就等于把它当 HTML 执行了（XSS）。textContent 永远当纯文本。
 */
function pushEvent(tag, detail, kind = "") {
  const row = document.createElement("div");
  row.className = "ev" + (kind ? " " + kind : "");

  const t = document.createElement("span");
  t.className = "t";
  t.textContent = `[${tag}] `;

  const n = document.createElement("span");
  n.className = "n";
  n.textContent = detail;

  row.append(t, n);
  els.events.appendChild(row);
  // 滚到底，否则新事件在可视区外面，看着像没反应
  els.events.scrollTop = els.events.scrollHeight;
}

/* ============================ markdown 渲染 ============================ */

/**
 * 先转义，再渲染。顺序不能反：
 *   如果先替换 markdown 再转义，`<strong>` 自己也会被转成 &lt;strong&gt;；
 *   如果只渲染不转义，模型输出里的 <img onerror=...> 会被浏览器执行。
 * 所以从头到尾只有「我们自己生成的那几个标签」是真正的 HTML。
 */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** 行内：`code` 和 **粗体**。作用在已转义的文本上，所以安全。 */
function inlineMd(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

/**
 * 极简 markdown：只处理模型实际会用的几种（标题 / 粗体 / 行内码 / 代码块 / 列表）。
 * 不是通用实现 —— 演示场景够用，多一种语法就多一处注入风险。
 */
function renderMarkdown(src) {
  const lines = escapeHtml(src).split("\n");
  const out = [];
  let inList = false;
  let inCode = false;

  for (const raw of lines) {
    if (raw.trim().startsWith("```")) {
      inCode = !inCode;
      out.push(inCode ? '<pre class="code">' : "</pre>");
      continue;
    }
    if (inCode) {
      out.push(raw);
      continue;
    }

    const isLi = /^\s*[-*] /.test(raw);
    if (isLi && !inList) { out.push("<ul>"); inList = true; }
    if (!isLi && inList) { out.push("</ul>"); inList = false; }

    if (isLi) {
      out.push("<li>" + inlineMd(raw.replace(/^\s*[-*] /, "")) + "</li>");
    } else if (/^### /.test(raw)) {
      out.push("<h4>" + inlineMd(raw.slice(4)) + "</h4>");
    } else if (/^## /.test(raw)) {
      out.push("<h3>" + inlineMd(raw.slice(3)) + "</h3>");
    } else if (/^# /.test(raw)) {
      out.push("<h3>" + inlineMd(raw.slice(2)) + "</h3>");
    } else if (raw.trim() === "") {
      out.push("");
    } else {
      out.push(inlineMd(raw) + "<br>");
    }
  }

  if (inList) out.push("</ul>");
  if (inCode) out.push("</pre>");
  return out.join("\n");
}

/* ============================ 事件分发 ============================ */

function handle(ev) {
  switch (ev.type) {
    case "start":
      threadId = ev.thread_id;
      pushEvent("start", `thread_id = ${ev.thread_id}`);
      setStatus("● 运行中…", true);
      break;

    case "tool_calls":
      pushEvent(`step ${ev.step}`, `模型决定调用 ${ev.names.join(", ")}`);
      break;

    case "tool_start": {
      // args 是对象（后端 _brief() 返回 dict），不能直接塞进模板字符串：
      // JS 会调 String(obj)，结果永远是 "[object Object]"。
      // 空参数就不显示，避免每行都拖一个 {}。
      const a = ev.args && Object.keys(ev.args).length
        ? " " + JSON.stringify(ev.args)
        : "";
      pushEvent(`step ${ev.step}`, `${ev.name} 执行中…${a}`);
      break;
    }

    case "tool_end":
      pushEvent(
        `step ${ev.step}`,
        `${ev.name} ${ev.ok ? "成功" : "失败"} · ${ev.latency_ms}ms`,
        ev.ok ? "ok" : "err"
      );
      break;

    case "tool_error":
      pushEvent(`step ${ev.step}`, `${ev.name} 参数非法：${ev.error}`, "err");
      break;

    case "answer":
      // 只有 answer 有正文，done 不重复带（契约规定）。
      // 走 renderMarkdown 而不是 textContent：模型返回的是 markdown 原文，
      // 直接塞进页面会看到一堆 ## 和 **。它内部已先转义再生成标签。
      els.answer.innerHTML = renderMarkdown(ev.content || "");
      break;

    case "done":
      setStatus("● 完成" + (threadId ? ` · ${threadId}` : ""));
      break;

    case "error":
      setStatus("● 服务端出错");
      pushEvent("error", ev.error || "未知错误", "err");
      break;

    default:
      // 契约外的事件也照实显示，不静默吞掉——新加事件时能立刻看见
      pushEvent(ev.type || "?", JSON.stringify(ev));
  }
}

/* ============================ 分帧解析 ============================ */

/**
 * 把一块原始文本（"event: x\ndata: {...}"）解析成对象。
 * 解析失败返回一个 parse_error 事件，而不是抛异常——
 * 让页面上能看见「哪一块坏了」，比控制台里一个红字有用。
 */
function parseBlock(block) {
  let type = "message";
  const dataLines = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      type = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
    // 以 ":" 开头的是注释行（SSE 心跳），按规范忽略
  }

  if (dataLines.length === 0) return null;

  const raw = dataLines.join("\n"); // 规范允许多行 data，用 \n 拼
  try {
    const obj = JSON.parse(raw);
    obj.type = obj.type || type; // data.type 是冗余兜底，缺失时用 event: 行
    return obj;
  } catch (e) {
    return { type: "parse_error", raw, error: String(e) };
  }
}

/* ============================ 主流程 ============================ */

async function run() {
  const input = els.q.value.trim();
  if (!input) return;

  els.go.disabled = true;
  clearView();
  setStatus("● 连接中…", true);

  try {
    const resp = await fetch("/agent/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input, max_turns: 12 }),
    });

    // resp.body 为 null 说明浏览器不支持流式读取（几乎不会，但别假定）
    if (!resp.ok || !resp.body) {
      setStatus(`● HTTP ${resp.status}`);
      pushEvent("error", await resp.text(), "err");
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      // stream:true —— 一个 UTF-8 中文字符占 3 字节，chunk 可能在字符中间切开。
      // 不带这个参数，残缺字节会被解成 � 且永久丢失。
      buf += decoder.decode(value, { stream: true });

      // TCP 是字节流不是消息流：一次 read 可能给半条、也可能给三条。
      const parts = buf.split("\n\n");
      buf = parts.pop(); // 最后一段可能不完整，留到下一次拼

      for (const block of parts) {
        const ev = parseBlock(block);
        if (ev) handle(ev);
      }
    }
  } catch (e) {
    setStatus("● 请求失败");
    pushEvent("error", String(e), "err");
  } finally {
    els.go.disabled = false;
  }
}

els.go.addEventListener("click", run);

els.q.addEventListener("keydown", (e) => {
  // isComposing：中文输入法按回车是「上屏」，不是「提交」。
  // 不判断的话，每打一个词都会误触发一次 Agent。
  if (e.key === "Enter" && !e.isComposing) run();
});
