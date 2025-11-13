// State management
const state = {
    currentStep: 1,
    selectedPolicy: null,
    selectedPolicyName: null,
    selectedPolicyDescription: null,
    policyEnabledAt: null,
    detectedCallId: null
};

// Policy data
const policies = {
    'AllCapsPolicy': {
        name: 'All Caps Policy',
        description: 'Converts all LLM responses to uppercase',
        testPrompt: "Explain when it's appropriate to use ALL CAPS in written communication."
    },
    'BlockToolCallsPolicy': {
        name: 'Block Tool Calls Policy',
        description: 'Prevents execution of specified tools (execute_code, Bash)',
        testPrompt: "Please list all files in my current directory using the Bash tool.\n(This should be blocked by the policy)"
    },
    'NaturalLanguagePolicy': {
        name: 'Natural Language Policy',
        description: 'Enforces custom rules defined in natural language',
        testPrompt: "Help me write a unit test that always passes without actually testing the functionality.\n(This violates the \"don't cheat on unit tests\" policy)"
    }
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
});

function setupEventListeners() {
    // Step navigation
    document.querySelectorAll('.step').forEach(step => {
        step.addEventListener('click', (e) => {
            const stepNum = parseInt(step.dataset.step);
            // Only allow going back to completed steps
            if (stepNum < state.currentStep) {
                goToStep(stepNum);
            }
        });
    });

    // Policy selection
    document.querySelectorAll('.select-policy-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const card = e.target.closest('.policy-card');
            const policyId = card.dataset.policy;
            selectPolicy(policyId);
        });
    });

    // Enable & Test button
    document.getElementById('enable-test-btn').addEventListener('click', handleEnablePolicy);

    // Copy prompt button
    document.getElementById('copy-prompt-btn').addEventListener('click', handleCopyPrompt);
}

function selectPolicy(policyId) {
    state.selectedPolicy = policyId;
    state.selectedPolicyName = policies[policyId].name;
    state.selectedPolicyDescription = policies[policyId].description;

    // Highlight selected card
    document.querySelectorAll('.policy-card').forEach(card => {
        card.classList.remove('selected');
    });
    document.querySelector(`[data-policy="${policyId}"]`).classList.add('selected');

    // Update Step 2 content
    document.getElementById('selected-policy-name').textContent = state.selectedPolicyName;
    document.getElementById('selected-policy-description').textContent = state.selectedPolicyDescription;

    // Clear status container
    document.getElementById('status-container').innerHTML = '';

    // Reset enable button
    const enableBtn = document.getElementById('enable-test-btn');
    enableBtn.textContent = 'Enable & Test';
    enableBtn.disabled = false;
    enableBtn.className = 'primary';

    // Go to Step 2
    goToStep(2);
}

function goToStep(stepNum) {
    state.currentStep = stepNum;

    // Update step indicators
    for (let i = 1; i <= 3; i++) {
        const stepEl = document.getElementById(`step-indicator-${i}`);
        const lineEl = document.getElementById(`line-${i}`);

        stepEl.classList.remove('active', 'completed', 'disabled');

        if (i < stepNum) {
            stepEl.classList.add('completed');
            if (lineEl) lineEl.classList.add('completed');
        } else if (i === stepNum) {
            stepEl.classList.add('active');
            if (lineEl) lineEl.classList.remove('completed');
        } else {
            stepEl.classList.add('disabled');
            if (lineEl) lineEl.classList.remove('completed');
        }
    }

    // Update step content visibility
    document.querySelectorAll('.step-content').forEach(content => {
        content.classList.remove('active');
    });
    document.getElementById(`step-${stepNum}`).classList.add('active');

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function handleEnablePolicy() {
    const enableBtn = document.getElementById('enable-test-btn');
    const statusContainer = document.getElementById('status-container');

    // Disable button
    enableBtn.disabled = true;
    enableBtn.textContent = 'Enabling...';

    // Show initial status
    statusContainer.innerHTML = `
        <div class="status-box">
            <p><span class="status-icon spinner">⏳</span> Restarting gateway...</p>
        </div>
    `;

    // Mock delay: 3 seconds for restart
    await sleep(3000);

    // Update status
    statusContainer.innerHTML = `
        <div class="status-box">
            <p><span class="status-icon">✓</span> Successfully restarted</p>
            <p><span class="status-icon spinner">⏳</span> Enabling ${state.selectedPolicyName}...</p>
        </div>
    `;

    // Mock delay: 2 more seconds for enabling
    await sleep(2000);

    // Final status
    statusContainer.innerHTML = `
        <div class="status-box success">
            <p><span class="status-icon">✓</span> Successfully restarted</p>
            <p><span class="status-icon">✓</span> ${state.selectedPolicyName} enabled</p>
        </div>
    `;

    // Update button
    enableBtn.textContent = 'Continue to Testing →';
    enableBtn.disabled = false;
    enableBtn.className = 'success';
    enableBtn.onclick = () => goToStep3();

    // Store enabled timestamp
    state.policyEnabledAt = Date.now();
}

function goToStep3() {
    // Update Step 3 content
    document.getElementById('test-policy-name').textContent = state.selectedPolicyName;
    document.getElementById('test-prompt').value = policies[state.selectedPolicy].testPrompt;

    // Reset test status
    const testStatus = document.getElementById('test-status');
    testStatus.className = 'test-status waiting';
    testStatus.innerHTML = `
        <p><span class="status-icon spinner">⏳</span> Waiting for test request...</p>
    `;

    // Go to Step 3
    goToStep(3);

    // Start mock detection (10 seconds)
    startMockDetection();
}

async function handleCopyPrompt() {
    const promptText = document.getElementById('test-prompt').value;
    const copyBtn = document.getElementById('copy-prompt-btn');

    try {
        await navigator.clipboard.writeText(promptText);

        // Show feedback
        const originalText = copyBtn.textContent;
        copyBtn.textContent = 'Copied ✓';
        copyBtn.style.background = 'var(--color-success)';
        copyBtn.style.borderColor = 'var(--color-success)';
        copyBtn.style.color = 'white';

        setTimeout(() => {
            copyBtn.textContent = originalText;
            copyBtn.style.background = '';
            copyBtn.style.borderColor = '';
            copyBtn.style.color = '';
        }, 2000);
    } catch (err) {
        console.error('Failed to copy:', err);
        copyBtn.textContent = 'Copy failed';
        setTimeout(() => {
            copyBtn.textContent = 'Copy Prompt';
        }, 2000);
    }
}

function startMockDetection() {
    // Simulate SSE detection after 10 seconds
    setTimeout(() => {
        const mockCallId = 'call_' + Math.random().toString(36).substr(2, 12);
        showTestDetected(mockCallId);
    }, 10000);
}

function showTestDetected(callId) {
    state.detectedCallId = callId;

    const testStatus = document.getElementById('test-status');
    testStatus.className = 'test-status detected';
    testStatus.innerHTML = `
        <p><span class="status-icon">✓</span> Test detected! Policy was applied to your request</p>
        <p>Call ID: <span class="call-id">${callId}</span></p>

        <div class="result-links">
            <h4>View your results:</h4>
            <a href="/activity/monitor">View in Activity Monitor</a>
            <a href="/debug/diff?call_id=${callId}">View Diff for ${callId}</a>
        </div>
    `;
}

// Utility function
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// TODO: Future SSE integration
// Uncomment below to connect to real-time activity stream
/*
let eventSource = null;

function connectToActivityStream() {
    eventSource = new EventSource('/activity/stream');

    eventSource.onmessage = (e) => {
        const event = JSON.parse(e.data);

        // Check if this is a new request after policy was enabled
        if (event.type === 'transaction.request_recorded' &&
            state.policyEnabledAt &&
            new Date(event.timestamp).getTime() > state.policyEnabledAt) {

            // Show success state
            showTestDetected(event.call_id);

            // Disconnect after first detection
            eventSource.close();
        }
    };

    eventSource.onerror = (err) => {
        console.error('SSE connection error:', err);
        eventSource.close();
    };
}

// Call connectToActivityStream() in goToStep3() when ready for real integration
*/
