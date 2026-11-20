# Voice-over studio repository instructions

## Scope and defaults

- Read [plan.md](plan.md) for milestones and [README.md](README.md) for the runnable setup. Keep the setup instructions current with the code.
- Work in milestone order. The initial milestone measures the owner's clone locally. Phase 1 is a single-owner studio with manual customer delivery. Billing, customer accounts, and automated delivery belong in Phase 2.
- Use Python 3.11 and a project-local environment. Target native Windows first and keep application paths and synthesis code compatible with Linux. Use `pathlib` and pass subprocess arguments as a list.
- Keep inference self-hostable and runnable without internet after setup. Record the code and weight revisions and their licenses when changing engines. Use the pinned official model for the trial. Keep engine-specific logic separate from script intake, jobs, and exports when building the app.

## Voice and customer files

- The sole production voice is the owner's. Keep reference enrollment owner-only. Use synthetic or explicitly authorized fixtures for testing. Preserve any watermark applied by the model.
- Keep voice samples, derived voice data (including trained adapters under `data/studio/trained/` and training result zips), scripts, recordings, model files, databases, and secrets outside version control. Use ignored local directories or an explicitly configured private data directory. Avoid logging script text and audio content.
- Bind the Phase 1 app to loopback by default. Validate file content and bounds, use generated IDs for stored paths, and keep private media outside static assets. Serve downloads through app routes with the applicable access checks.

## Verification

- For changes to the trial, check `python scripts/voice_trial.py --help`, input validation, and WAV output with the real engine when local weights are available. Inspect output format with FFprobe and disclose which device and fixtures were used.
- Run the tests with `.\.tools\directed-env\Scripts\python.exe -m unittest discover -s tests` on Windows. Tests that supply a fake engine must not depend on local model files; pass a fake quality checker when a test needs checks.
- For changes to the studio, verify the affected browser workflow and run model-free tests for job and file handling. Test inference separately with local weights. Keep prior valid exports intact on retry or failed regeneration.
