// ABOUTME: Demo interface JavaScript for AI Control demonstration
// ABOUTME: Handles demo execution (both static and live), live updates, and result visualization

const SAFE_ATTR_PATTERN = /^[a-zA-Z_][\w:-]*$/;

function sanitizeText(value) {
  if (value == null) return "";
  const text = String(value);
  return text.replace(/[\u2028\u2029]/g, "");
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") {
      node.className = value;
    } else if (key === "text") {
      node.textContent = sanitizeText(value);
    } else if (key === "dataset" && value && typeof value === "object") {
      for (const [dataKey, dataValue] of Object.entries(value)) {
        node.dataset[dataKey] = sanitizeText(dataValue);
      }
    } else {
      if (typeof key === "string" && key.toLowerCase().startsWith("on")) {
        throw new Error(`Event handler attributes are not allowed (saw ${key})`);
      }
      if (typeof key === "string" && !SAFE_ATTR_PATTERN.test(key)) {
        throw new Error(`Attribute name ${key} contains unsupported characters`);
      }
      const safeValue = typeof value === "string" ? sanitizeText(value) : value;
      node.setAttribute(key, safeValue);
    }
  }
  for (const child of children) {
    if (child == null) continue;
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(sanitizeText(child)));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

const state = {
  harmfulCallId: null,
  protectedCallId: null,
  eventSource: null,
  demoRunning: false,
  demoMode: "fake",
};

function getDemoMode() {
  const selected = document.querySelector('input[name="demo-mode"]:checked');
  return selected ? selected.value : "fake";
}

function setDemoStatus(text) {
  const statusEl = document.getElementById("demo-status-text");
  if (statusEl) {
    statusEl.textContent = text;
  }
}

function setRunButtonState(enabled) {
  const btn = document.getElementById("run-demo");
  if (btn) {
    btn.disabled = !enabled;
  }
}

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  return await res.json();
}

function renderToolCall(toolCall) {
  const func = toolCall.function || {};
  const name = func.name || "unknown";
  let args = {};

  try {
    args = typeof func.arguments === "string" ? JSON.parse(func.arguments) : func.arguments;
  } catch (e) {
    args = { raw: func.arguments };
  }

  const query = args.query || args.raw || "";
  const isHarmful = query.toUpperCase().includes("DROP");

  const toolCallDiv = el("div", { class: "tool-call" });

  const nameDiv = el("div", { class: "tool-call-name", text: `🔧 ${name}` });
  toolCallDiv.appendChild(nameDiv);

  if (query) {
    const sqlDiv = el("div", { class: "tool-call-sql", text: `SQL: ${query}` });
    toolCallDiv.appendChild(sqlDiv);
  }

  if (isHarmful) {
    const warningDiv = el("div", { class: "tool-call-warning" });
    warningDiv.appendChild(el("span", { text: "⚠️" }));
    warningDiv.appendChild(el("span", { text: "DESTRUCTIVE OPERATION DETECTED" }));
    toolCallDiv.appendChild(warningDiv);
  }

  return toolCallDiv;
}

function renderTextContent(content) {
  const div = el("div", { class: "text-content" });
  div.appendChild(el("p", { text: content }));
  return div;
}

function updateHarmfulResponse(message) {
  const responseBox = document.getElementById("harmful-response");
  if (!responseBox) return;

  responseBox.className = "response-box";
  responseBox.innerHTML = "";

  const content = message.content;
  const toolCalls = message.tool_calls || [];

  if (content) {
    responseBox.appendChild(renderTextContent(content));
  }

  if (toolCalls && toolCalls.length > 0) {
    for (const toolCall of toolCalls) {
      responseBox.appendChild(renderToolCall(toolCall));
    }
  }
}

function updateProtectedResponse(message) {
  const responseBox = document.getElementById("protected-response");
  if (!responseBox) return;

  responseBox.className = "response-box";
  responseBox.innerHTML = "";

  const content = message.content;
  const toolCalls = message.tool_calls || [];

  if (content) {
    if (content.includes("BLOCKED") || content.includes("blocked")) {
      const blockedDiv = el("div", { class: "decision-blocked" });
      blockedDiv.appendChild(el("div", { class: "decision-verdict", text: "🛡️ REQUEST BLOCKED" }));
      blockedDiv.appendChild(el("div", { text: content }));
      responseBox.appendChild(blockedDiv);
    } else {
      responseBox.appendChild(renderTextContent(content));
    }
  }

  if (toolCalls && toolCalls.length > 0) {
    for (const toolCall of toolCalls) {
      responseBox.appendChild(renderToolCall(toolCall));
    }
  }
}

function updatePolicyDecision(policyEvent) {
  const decisionBox = document.getElementById("protected-decision");
  if (!decisionBox) return;

  decisionBox.innerHTML = "";

  const action = policyEvent.action || "unknown";
  const reasoning = policyEvent.reasoning || policyEvent.details || "No reasoning provided";

  if (action === "block" || action === "blocked") {
    decisionBox.className = "decision-box decision-blocked";

    const verdictDiv = el("div", { class: "decision-verdict", text: "🚫 BLOCKED" });
    decisionBox.appendChild(verdictDiv);

    const reasonDiv = el("div", { class: "decision-reasoning" });
    reasonDiv.appendChild(el("strong", { text: "Reasoning: " }));
    reasonDiv.appendChild(el("span", { text: reasoning }));
    decisionBox.appendChild(reasonDiv);

  } else {
    decisionBox.className = "decision-box decision-allowed";

    const verdictDiv = el("div", { class: "decision-verdict", text: "✅ ALLOWED" });
    decisionBox.appendChild(verdictDiv);

    const reasonDiv = el("div", { class: "decision-reasoning" });
    reasonDiv.appendChild(el("strong", { text: "Reasoning: " }));
    reasonDiv.appendChild(el("span", { text: reasoning }));
    decisionBox.appendChild(reasonDiv);
  }
}

function updateHarmfulResult(success) {
  const resultBox = document.getElementById("harmful-result");
  if (!resultBox) return;

  resultBox.className = "result-box result-harmful";
  resultBox.innerHTML = "";

  if (success) {
    resultBox.appendChild(el("div", { text: "❌ HARMFUL SQL EXECUTED" }));
    resultBox.appendChild(el("div", { text: "The DROP TABLE command was executed, destroying data." }));
  } else {
    resultBox.appendChild(el("div", { text: "⚠️ Request completed without protection" }));
  }
}

function updateProtectedResult(blocked) {
  const resultBox = document.getElementById("protected-result");
  if (!resultBox) return;

  resultBox.className = "result-box result-protected";
  resultBox.innerHTML = "";

  if (blocked) {
    resultBox.appendChild(el("div", { text: "✅ THREAT PREVENTED" }));
    resultBox.appendChild(el("div", { text: "AI Control successfully blocked the harmful operation." }));
  } else {
    resultBox.appendChild(el("div", { text: "✅ Safe request allowed" }));
  }
}

async function runDemo() {
  if (state.demoRunning) return;

  state.demoRunning = true;
  state.demoMode = getDemoMode();
  setRunButtonState(false);

  // Reset UI
  resetDemo();

  try {
    if (state.demoMode === "fake") {
      await runFakeDemo();
    } else if (state.demoMode === "static") {
      await runStaticDemo();
    } else {
      await runLiveDemo();
    }
  } catch (err) {
    console.error("Demo error:", err);
    setDemoStatus(`Error: ${err.message}`);
  } finally {
    state.demoRunning = false;
    setRunButtonState(true);
  }
}

async function runFakeDemo() {
  setDemoStatus("Loading animated demo...");

  // Fetch pre-defined examples
  const examples = await fetchJSON("/demo/examples");

  // Simulate demo progression with delays for effect
  setDemoStatus("Part 1: Showing scenario WITHOUT AI Control...");
  await new Promise(resolve => setTimeout(resolve, 1000));

  // Display harmful scenario
  const harmful = examples.harmful_example;
  if (harmful.ai_response && harmful.ai_response.tool_calls) {
    const message = {
      tool_calls: harmful.ai_response.tool_calls
    };
    updateHarmfulResponse(message);
  }

  const harmfulResultBox = document.getElementById("harmful-result");
  if (harmfulResultBox) {
    harmfulResultBox.className = "result-box result-harmful";
    harmfulResultBox.innerHTML = "";
    harmfulResultBox.appendChild(el("div", { text: harmful.result }));
  }

  await new Promise(resolve => setTimeout(resolve, 1500));

  setDemoStatus("Part 2: Showing scenario WITH AI Control...");
  await new Promise(resolve => setTimeout(resolve, 1000));

  // Display protected scenario
  const protected = examples.protected_example;

  if (protected.ai_response && protected.ai_response.content) {
    const message = {
      content: protected.ai_response.content
    };
    updateProtectedResponse(message);
  }

  if (protected.policy_decision) {
    updatePolicyDecision(protected.policy_decision);
  }

  const protectedResultBox = document.getElementById("protected-result");
  if (protectedResultBox) {
    protectedResultBox.className = "result-box result-protected";
    protectedResultBox.innerHTML = "";
    protectedResultBox.appendChild(el("div", { text: protected.result }));
  }

  await new Promise(resolve => setTimeout(resolve, 500));
  setDemoStatus("Demo complete! (Animated demo)");
}

async function runStaticDemo() {
  setDemoStatus("Loading static examples...");

  // Fetch pre-defined examples
  const examples = await fetchJSON("/demo/examples");

  // Display both scenarios immediately without animation
  const harmful = examples.harmful_example;
  if (harmful.ai_response && harmful.ai_response.tool_calls) {
    const message = {
      tool_calls: harmful.ai_response.tool_calls
    };
    updateHarmfulResponse(message);
  }

  const harmfulResultBox = document.getElementById("harmful-result");
  if (harmfulResultBox) {
    harmfulResultBox.className = "result-box result-harmful";
    harmfulResultBox.innerHTML = "";
    harmfulResultBox.appendChild(el("div", { text: harmful.result }));
  }

  const protected = examples.protected_example;

  if (protected.ai_response && protected.ai_response.content) {
    const message = {
      content: protected.ai_response.content
    };
    updateProtectedResponse(message);
  }

  if (protected.policy_decision) {
    updatePolicyDecision(protected.policy_decision);
  }

  const protectedResultBox = document.getElementById("protected-result");
  if (protectedResultBox) {
    protectedResultBox.className = "result-box result-protected";
    protectedResultBox.innerHTML = "";
    protectedResultBox.appendChild(el("div", { text: protected.result }));
  }

  setDemoStatus("Demo loaded! (Static examples)");
}

async function runLiveDemo() {
  setDemoStatus("Running LIVE demo - making real requests...");

  const prompt = "Show me customer 123";

  // Make live request
  setDemoStatus("Sending request through proxy with current policy...");

  const response = await fetchJSON("/demo/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, mode: "live" })
  });

  state.protectedCallId = response.call_id;

  await new Promise(resolve => setTimeout(resolve, 2000));

  // Fetch the actual conversation data
  if (response.call_id && response.call_id !== "unknown") {
    setDemoStatus("Loading conversation data...");
    await monitorCall(response.call_id, true);
  } else if (response.status === "blocked") {
    // Show blocked result
    updatePolicyDecision({
      verdict: "BLOCKED",
      reasoning: "Request was blocked by policy",
      action: "block"
    });

    const protectedResultBox = document.getElementById("protected-result");
    if (protectedResultBox) {
      protectedResultBox.className = "result-box result-protected";
      protectedResultBox.innerHTML = "";
      protectedResultBox.appendChild(el("div", { text: "✅ THREAT PREVENTED - Policy blocked the request" }));
    }
  }

  setDemoStatus("Live demo complete!");
}

async function monitorCall(callId, isProtected) {
  try {
    const snapshot = await fetchJSON(`/api/hooks/conversation?call_id=${callId}`);

    if (snapshot.response?.choices) {
      const choices = snapshot.response.choices;
      if (choices.length > 0 && choices[0].message) {
        const message = choices[0].message;

        if (isProtected) {
          updateProtectedResponse(message);

          const toolCalls = message.tool_calls || [];
          const hasHarmful = toolCalls.some(tc => {
            try {
              const args = typeof tc.function.arguments === "string"
                ? JSON.parse(tc.function.arguments)
                : tc.function.arguments;
              return (args.query || "").toUpperCase().includes("DROP");
            } catch {
              return false;
            }
          });

          updateProtectedResult(!hasHarmful);
        }
      }
    }

    // Check for policy events
    if (isProtected && snapshot.policy_events && snapshot.policy_events.length > 0) {
      const latestEvent = snapshot.policy_events[snapshot.policy_events.length - 1];
      updatePolicyDecision(latestEvent);
    }

  } catch (err) {
    console.error(`Error monitoring call ${callId}:`, err);
  }
}

function resetDemo() {
  // Clear harmful side
  const harmfulResponse = document.getElementById("harmful-response");
  if (harmfulResponse) {
    harmfulResponse.className = "response-box response-empty";
    harmfulResponse.innerHTML = '<div class="empty-state">Waiting for demo to run...</div>';
  }

  const harmfulResult = document.getElementById("harmful-result");
  if (harmfulResult) {
    harmfulResult.className = "result-box result-empty";
    harmfulResult.innerHTML = '<div class="empty-state">No activity yet</div>';
  }

  // Clear protected side
  const protectedResponse = document.getElementById("protected-response");
  if (protectedResponse) {
    protectedResponse.className = "response-box response-empty";
    protectedResponse.innerHTML = '<div class="empty-state">Waiting for demo to run...</div>';
  }

  const protectedDecision = document.getElementById("protected-decision");
  if (protectedDecision) {
    protectedDecision.className = "decision-box decision-empty";
    protectedDecision.innerHTML = '<div class="empty-state">No activity yet</div>';
  }

  const protectedResult = document.getElementById("protected-result");
  if (protectedResult) {
    protectedResult.className = "result-box result-empty";
    protectedResult.innerHTML = '<div class="empty-state">No activity yet</div>';
  }

  state.harmfulCallId = null;
  state.protectedCallId = null;

  const mode = getDemoMode();
  setDemoStatus(`Ready to run (${mode} mode)`);
}

// Initialize
document.addEventListener("DOMContentLoaded", () => {
  const runBtn = document.getElementById("run-demo");
  if (runBtn) {
    runBtn.addEventListener("click", runDemo);
  }

  const resetBtn = document.getElementById("reset-demo");
  if (resetBtn) {
    resetBtn.addEventListener("click", resetDemo);
  }

  // Listen for mode changes
  const modeRadios = document.querySelectorAll('input[name="demo-mode"]');
  modeRadios.forEach(radio => {
    radio.addEventListener("change", () => {
      const mode = getDemoMode();
      setDemoStatus(`Ready to run (${mode} mode)`);
    });
  });

  setDemoStatus("Ready to run (animated demo mode)");
});
