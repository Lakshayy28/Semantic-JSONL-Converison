You are an Expert AI Architect and SDET (Software Development Engineer in Test). 

I have an existing GenAI utility codebase that interacts with a proprietary LLM gateway called "Tachyon." Currently, the code takes a user prompt, does a single vector search against a chunked rules document, and tries to generate BDD test scenarios and API payloads in one single Chat Completion call. 

This single-shot approach is failing. It produces hallucinated JSON payloads, violates Swagger schemas, and misses complex business rules. 

I need you to refactor this codebase into an **Agentic State Machine Workflow**. The agent must think through the decision workflow, generate its own search queries, synthesize scenarios, map payloads, and validate them.

### System Context & API Parameters
1. **Tachyon Search API:** I use this to search my vector DB (which contains my chunked business rules documents). 
   * **Mandatory Parameters for this build:** `"searchType": "hybrid"`, `"reRanker": "RRF"` (because the semantic ranker truncates at 512 tokens, we must use RRF), `"fieldFilters": {"document_id": ["<file_id>"]}`.
2. **Tachyon Chat Completion:** Standard OpenAI-compatible chat endpoint.
3. **Inputs Available:** The user's natural language prompt, the `document_id` of the chunked rules, and the target API's Swagger schema dictionary.

### The Target Architecture (The Agentic State Graph)
Implement a Python class called `APITestAgentOrchestrator` that manages a `state` dictionary (e.g., `{"user_prompt": str, "search_queries": list, "retrieved_rules": str, "bdd_scenarios": list, "payloads": dict, "test_results": dict}`).

Implement the following distinct steps as methods that update this state sequentially:

**Step 1: `_plan_retrieval()` (LLM Call 1)**
* **System Prompt:** "You are an API testing planner. Analyze the user prompt. Generate a JSON list of 3 highly specific search queries to retrieve the exact business rules, boundary conditions, and acronyms needed from the vector database to test this workflow."

**Step 2: `_execute_search()` (Tool Call)**
* Iterate through the generated search queries. Hit the Tachyon Search API using `hybrid` and `RRF`. Aggregate the `raw_context` from the top results, deduplicate them, and save to state.

**Step 3: `_generate_bdd_scenarios()` (LLM Call 2)**
* **System Prompt:** "You are an Expert QA Architect. Using the provided Business Rules Context, generate {X} BDD scenarios (Given/When/Then). Ensure you explicitly state the exact data conditions (e.g., 'Income > 100k') required by the rules."

**Step 4: `_synthesize_payloads()` (LLM Call 3)**
* **System Prompt:** "You are an API Data Engineer. For the given BDD scenario and the provided Swagger Schema, generate the exact, valid JSON POST payload required. Do not hallucinate fields not in the Swagger. Return ONLY raw JSON." 
* *Iterate this over the generated scenarios.*

**Step 5: `_execution_and_critic_loop()` (The Agentic Loop)**
* Create a mock execution method (or use the `requests` library to hit a test endpoint if a URL is provided).
* **The Loop:** If the API returns a 400-level error (Schema Validation Failure), pass the Error Response, the Swagger Schema, and the Failed Payload to the LLM. 
* **Critic Prompt:** "The API rejected this payload with the following error. Fix the JSON payload to resolve this schema violation." 
* Allow a maximum of 3 retries before marking the scenario as "Failed".

### Your Task
1. Analyze the existing codebase I provide below.
2. Rewrite the orchestration logic to implement this 5-step Agentic Workflow.
3. Ensure the code is strictly typed, uses `logging` extensively (so I can see the agent's "thought process" in the console), and handles Tachyon API timeouts gracefully using `tenacity`.
4. Provide the complete refactored orchestration code.

[... I WILL PASTE MY EXISTING CODE HERE ...]
