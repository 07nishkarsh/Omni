const form = document.getElementById('application-form');
const viewForm = document.getElementById('view-form');
const viewStatus = document.getElementById('view-status');
const txIdDisplay = document.querySelector('#tx-id-display span');
const statusStepsContainer = document.getElementById('status-steps');
const newAppBtn = document.getElementById('new-app-btn');
const submitBtn = document.getElementById('submit-btn');

let pollInterval;
let currentTransactionId = null;

// The 10 explicit steps we want to display
const BASE_STEPS = [
    { id: 1, title: "Application received & validated" },
    { id: 2, title: "Agent A — routing decision" },
    { id: 3, title: "Agent B — compliance check" },
    { id: 4, title: "Agent C — treasury check" },
    { id: 5, title: "Income Proportionality Score" },
    { id: 6, title: "Negotiation" }, // conditionally shown
    { id: 7, title: "Validator — final determination" },
    { id: 8, title: "Verdict issued" },
    { id: 9, title: "Manager decision" }, // conditionally shown
    { id: 10, title: "Credit/disbursement confirmed" }
];

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    
    // Disable button to prevent double-submit
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';

    const payload = {
        customer_name: document.getElementById('customer_name').value,
        loan_type: document.getElementById('loan_type').value,
        requested_amount: parseFloat(document.getElementById('requested_amount').value),
        annual_declared_income: parseFloat(document.getElementById('annual_declared_income').value),
        is_urgent: document.getElementById('is_urgent').checked,
        target_fund: document.getElementById('target_fund').value
    };

    try {
        const response = await fetch('/api/v1/applications', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (!response.ok) throw new Error('Submission failed');
        
        const data = await response.json();
        currentTransactionId = data.transaction_id;
        
        startStatusPolling(currentTransactionId);
        showStatusView(currentTransactionId);
    } catch (err) {
        alert(err.message);
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit Application';
    }
});

newAppBtn.addEventListener('click', () => {
    clearInterval(pollInterval);
    form.reset();
    submitBtn.disabled = false;
    submitBtn.textContent = 'Submit Application';
    viewStatus.classList.remove('active');
    viewForm.classList.add('active');
    newAppBtn.style.display = 'none';
});

function showStatusView(txId) {
    viewForm.classList.remove('active');
    viewStatus.classList.add('active');
    txIdDisplay.textContent = txId;
    
    // Initial render of greyed out steps
    renderSteps([], false, false);
}

function startStatusPolling(txId) {
    if (pollInterval) clearInterval(pollInterval);
    
    // Poll immediately, then every 1.5s
    fetchStatus(txId);
    pollInterval = setInterval(() => fetchStatus(txId), 1500);
}

async function fetchStatus(txId) {
    try {
        const response = await fetch(`/api/v1/applications/${txId}/status`);
        if (!response.ok) return;
        
        const data = await response.json();
        
        // Check if finished
        const isFinished = ['APPROVED', 'REJECTED', 'CANCELLED'].includes(data.status);
        if (isFinished) {
            clearInterval(pollInterval);
            newAppBtn.style.display = 'block';
        }
        
        // Does it have negotiation?
        const hasNegotiation = data.steps.some(s => s.step_num === 6);
        
        renderSteps(data.steps, data.requires_human_review, hasNegotiation);
        
    } catch (err) {
        console.error("Polling error:", err);
    }
}

function renderSteps(activeSteps, requiresHumanReview, hasNegotiation) {
    // Filter base steps based on conditions
    let visibleSteps = BASE_STEPS.filter(step => {
        if (step.id === 6 && !hasNegotiation) return false;
        if (step.id === 9 && !requiresHumanReview) return false;
        return true;
    });

    statusStepsContainer.innerHTML = '';
    
    visibleSteps.forEach(baseStep => {
        // Find if this step is completed in the active steps array
        const completedStep = activeSteps.find(s => s.step_num === baseStep.id);
        
        const li = document.createElement('li');
        li.className = `step-item ${completedStep ? 'active' : ''}`;
        
        let iconClass = 'step-icon';
        if (completedStep) {
            iconClass += ' checkmark';
            if (completedStep.detail.includes('FAILED') || completedStep.detail.includes('REJECTED') || completedStep.detail.includes('Vetoed')) {
                li.className += ' error';
                iconClass = iconClass.replace('checkmark', 'crossmark');
            } else if (completedStep.detail.includes('COUNTERED') || completedStep.detail.includes('Pending')) {
                li.className += ' warning';
                iconClass = iconClass.replace('checkmark', 'crossmark'); // Could use a different icon
            }
        }

        li.innerHTML = `
            <div class="${iconClass}"></div>
            <div class="step-content">
                <div class="step-title">${baseStep.title}</div>
                <div class="step-detail">${completedStep ? completedStep.detail : 'Awaiting...'}</div>
            </div>
        `;
        
        statusStepsContainer.appendChild(li);
    });
}
