# guardrails

Phase 3 service. It will validate the agent's input and output:

- prompt-injection detection (rules + LLM classifier),
- masking of sensitive data (DNI, CBU, card numbers),
- schema validation of the agent's response.
