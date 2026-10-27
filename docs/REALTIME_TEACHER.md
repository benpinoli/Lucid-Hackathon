# Single-model Realtime teacher

On `one_model`, **gpt-realtime-2.1** owns the conversation, lesson planning,
visual code, new concepts, hints and assessment. Only `OPENAI_API_KEY` is used. There are no
server-side LLM calls. The original provider adapters remain only for offline regression tests.

1. The browser creates an authenticated lesson, then requests a short-lived Realtime credential
   bound to it. The backend preloads brain context, learner notes, current screen and review data.
2. Audio flows over WebRTC. Typed turns use the same transport with text output and no microphone.
   Enabling voice after a text session reconnects with the latest server context and notes.
3. The model requests local functions. The browser calls the authenticated lesson tool endpoint.
   Context, concepts, notes, lesson compilation and assessment run locally, with no second model.
4. `present_lesson` compiles the existing diagram/expression language and publishes one step.
   Normal scene schemas, object/byte budgets and animation validation still apply. Validation
   errors go back to the same Realtime model for repair. Arbitrary code is never evaluated.
5. Once the visual is ready, the model speaks that step and listens. `advance_step` reveals another
   prepared step only when requested. Replaying a tool call does not publish the scene twice.
6. Questions have a stored rubric and ID before an answer is graded. Independent retrieval updates
   mastery and the review scheduler; predictions only log feedback, synthesis rates a direct edge,
   and skipping changes no score. Notes remain scoped to the authenticated learner.

Tools: `get_learning_context`, `remember_concepts`, `present_lesson`, `advance_step`, `load_lesson`,
`ask_question`, `record_assessment`, `skip_question`, and `learner_memory`.

The browser cancels speech and advances the server epoch on interruption. Tool results from the old
generation are discarded. A reconnect preloads the retained screen and durable brain/notes; raw
provider conversation history is not reconstructed.

Runtime: `backend/app/realtime_tools.py`, `backend/app/voice.py`, `frontend/src/realtime.ts`.
Shared teaching rules and schemas: `backend/app/teaching.py`. The Jay Cummings style guidance,
problem-first sequence, concrete visuals, LaTeX and parameter animation requirements are retained.

Without an OpenAI key, authored examples still work locally. A live-key acceptance run is needed
to measure speech quality, provider schema acceptance and end-to-end latency. Offline tests mock
only the OpenAI transport while exercising the real local authorization, brain, compiler and renderer.
