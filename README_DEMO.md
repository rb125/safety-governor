# Reliability Layer Agent - Rich UI Demo

This demo provides an immersive, interactive CLI experience for the Reliability Layer Agent.

## Features
- **Live Dashboard:** Real-time visualization of webserver logs, system metrics, and agent status.
- **Agentic Workflow:** detailed visualization of the agent's "Thinking" process, Tool selection, and reliability checks.
- **Reliability Guardrails:** Visualizes the CDCT (Context), DDFT (Fabrication), and EECT (Epistemic Confidence) checks.
- **Integration Simulation:** Demonstrates Slack notifications and Jira ticket creation.
- **Robustness:** Gracefully handles missing Elasticsearch indices or API failures by falling back to high-fidelity simulations.

## Usage

1. **Install Dependencies:**
   ```bash
   pip install rich python-dotenv
   ```

2. **Run the Demo:**
   ```bash
   python3 demo_rich_agentic.py
   ```

## Scenario
The demo simulates a "High Severity" incident:
1. **Monitoring:** Normal traffic flows.
2. **Incident:** A sudden spike in 500 errors on the `payment-service`.
3. **Detection:** The agent detects the anomaly.
4. **Planning:** The agent searches the Knowledge Base (Elasticsearch) for runbooks.
5. **Verification:** The agent validates the proposed plan (restart pods) against reliability metrics.
6. **Execution:** The agent executes the fix (kubectl rollout).
7. **Recovery:** Service health is restored.
8. **Reporting:** The agent updates Slack and creates a Jira ticket.
