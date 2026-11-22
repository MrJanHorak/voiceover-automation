# Voice-over studio repository instructions

## Scope and defaults

- Read [plan.md](plan.md) for milestones and [README.md](README.md) for the runnable setup. Keep the setup instructions current with the code.
- Work in milestone order (see plan.md). The studio has an admin role that creates and publishes voices, and a customer role that records with published voices. Hosting, email and billing are Phase 2 steps 3 and 4.
- Use Python 3.11 and a project-local environment. Local development targets native Windows; the online deployment runs on Linux (deploy/, worker/), so keep paths and synthesis code compatible with both. Use `pathlib` and pass subprocess arguments as a list.
- Keep inference self-hostable and runnable without internet after setup. Record the code and weight revisions and their licenses when changing engines. Use the pinned official model for the trial. Keep engine-specific logic separate from script intake, jobs, and exports when building the app.

## Voice and customer files

- Only voices with the speaker's recorded consent may be offered to customers. Keep reference enrollment, training and trained-voice upload admin-only. Use synthetic or explicitly authorized fixtures for testing; never use real voice recordings, the studio database or trained voices as test fixtures. Preserve any watermark applied by the model.
- Keep voice samples, derived voice data (including trained adapters under `data/studio/trained/` and training result zips), scripts, recordings, model files, databases, and secrets outside version control. Use ignored local directories or an explicitly configured private data directory. Avoid logging script text and audio content.
- Every route needs an explicit access rule in `studio/app.py`: `admin_only`/`admin_mutations` for voice, training, intake and account features; `job_access`/`job_mutations` for anything under `/api/jobs/{job_id}`, where customers reach only their own recordings and get 404 otherwise; and `mutations` (signed in) for the rest. Customers must never see engine names, model details, consent records or other accounts' data (`customer_view`, `public_profile`). A voice profile may be offered to customers only while published with consent on record (`Store.consent_problem`); keep consent documents and trained voices out of Git. Tests that are not about accounts pass `auth=False` to `create_app`.
- Split hosting: `studio/remote.py` (VPS side) and `worker/handler.py` (GPU side) share the job format documented in `worker/README.md`. Change both together, and rebuild the worker image when `studio/audio.py` or `studio/trained.py` changes. The worker route `/api/worker/adapters/...` needs `VOICEOVER_WORKER_TOKEN` (at least 32 characters) and serves only trained-voice files. Never create, start or delete paid cloud resources (VPS, Runpod endpoints) without the owner's explicit approval of the price.
- Bind the Phase 1 app to loopback by default. Validate file content and bounds, use generated IDs for stored paths, and keep private media outside static assets. Serve downloads through app routes with the applicable access checks.

## Verification

- For changes to the trial, check `python scripts/voice_trial.py --help`, input validation, and WAV output with the real engine when local weights are available. Inspect output format with FFprobe and disclose which device and fixtures were used.
- Run the tests with `.\.tools\directed-env\Scripts\python.exe -m unittest discover -s tests` on Windows. Tests that supply a fake engine must not depend on local model files; pass a fake quality checker when a test needs checks.
- After visible interface changes, regenerate the README screenshots with `docs/screenshots/demo_server.py` and `capture.py`. They use invented demo data only; never capture real accounts, voices or scripts.
- For changes to the studio, verify the affected browser workflow and run model-free tests for job and file handling. Test inference separately with local weights. Keep prior valid exports intact on retry or failed regeneration.
