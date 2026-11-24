I am working in this workspace to repair a task package that failed its Implementation QC. The task files are currently located in the "T01 — 4 of 5" directory. Please analyze the files in this directory and execute the following specific fixes:

1. Rename the main task folder from its pipeline slug to match the challenge name (e.g., `censored-exposure-forecasting`).
2. Navigate to the `authoring/` folder and delete the extraneous data generator and model scripts.
3. Update the verifier test script (likely `tests/test.sh` or similar) so that when it runs pytest, it emits a CTRF report exactly to `/logs/verifier/ctrf.json`.
4. Open `instruction.md` and update the runtime instructions to explicitly disclose the 180-second runtime budget (currently it only mentions 120 seconds).
5. Open the agent's Docker image file (`environment/Dockerfile`) and remove `pytest`, as it is a test-only dependency that violates environment hygiene.
6. Create a new reviewer-facing `README.md` file in the root of the task directory that summarizes the task, deliverable, and scoring based on the instructions.

Please perform these file operations and let me know when you are done.
