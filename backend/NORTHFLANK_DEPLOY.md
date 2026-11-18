# LearningX — Northflank shared API deployment

Status: SOURCE READY / CLOUD DEPLOYMENT NOT YET VERIFIED. Do not upload real course materials.

## 1. Ownership and dependencies

- Northflank team: masterkungfu's team (Free), linked GitHub account zxmk9123-creator.
- The repository zxmk9123-creator/LearningX appears in Northflank Connected Repositories; this was verified in the connected browser tab.
- This GitHub branch is based on the unmerged ingestion PR #6 (feat/neon-durable-course-artifacts), not main. The demo PR #7 is also unmerged and is a separate branch.
- Existing Vercel LearningX frontend and unrelated Oil&Fat projects must not be modified.

## 2. Topology

1. Northflank project learningx (new, separate from Default).
2. Public combined service learningx-shared-api, Docker container running FastAPI on HTTP port 8080, no idle sleep.
3. Private combined service learningx-ingest-worker, the SAME container image, command override:
       python -m learningx.northflank_worker
   No public port. It polls the Neon PostgreSQL ingestion queue.
4. Shared existing Neon database learningx-pilot / learningx and private S3-compatible bucket learningx-private-lessons.
5. Existing static frontend learning-x-one.vercel.app remains separate.

Both containers should use the same reviewed Git commit and an explicitly configured secret group. Check that the Northflank Free account quota actually supports two running services and any storage/network usage before deploying.

## 3. Build settings

For EACH combined service:
- Source: GitHub zxmk9123-creator/LearningX
- Branch: feat/northflank-shared-api for a preproduction pilot
- Build type: Dockerfile
- Build context: /backend
- Dockerfile path: /backend/Dockerfile
- Build engine: BuildKit
- API startup command: use Docker CMD (uvicorn learningx.api:app --host 0.0.0.0 --port 8080)
- Worker startup override: python -m learningx.northflank_worker
- API networking: PUBLIC HTTP port 8080, only after prerequisites validated
- Worker networking: NO public port
- API HTTP liveness/readiness probe: /health on port 8080
- API DB check: GET /health/db, must report connected
- Region: Singapore if available (Neon is Singapore); otherwise confirm a nearby supported region
- Initially disable automatic production deploys until security and integration tests pass

## 4. Runtime secrets

Set these through Northflank Secret Groups (NEVER GitHub, Dockerfile, build args, or frontend):

- DATABASE_URL: Neon PostgreSQL connection URI to database learningx; use a protected Neon role/password
- AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY: private Neon Storage S3 credentials
- AWS_ENDPOINT_URL_S3: exact Neon Storage S3 endpoint
- AWS_REGION: exact Neon Storage region, usually ap-southeast-1
- NIT_STORAGE_BUCKET: learningx-private-lessons (private bucket, already created)
- NIT_JWT_SECRET: unique strong secret, >=32 characters
- NIT_REVIEW_SIGNING_KEY: a DIFFERENT strong secret, >=32 characters
- NIT_CORS_ORIGINS: https://learning-x-one.vercel.app
- NIT_WORKER_BACKEND: northflank_poll for both API and worker
- NIT_WORKER_POLL_SECONDS: 8 for the worker (optional)
- NIT_LLM_BASE_URL, NIT_LLM_MODEL, NIT_LLM_API_KEY: actual verified model provider configuration, server only

Built into the image: NIT_HOSTED_RUNTIME=1 (no SQLite or local artifacts allowed), NIT_ENGINE_ROOT=/app/nit_engine and port 8080.
An empty/missing DATABASE_URL causes startup failure intentionally.

## 5. Browser upload and data protection

- Neon bucket CORS must allow the exact Vercel frontend origin and PUT with Content-Type. The bucket remains PRIVATE.
- API issues a time-limited PUT URL; the browser uploads directly to Neon rather than through the API.
- Upload completion checks remote object size, and the worker recomputes its SHA-256.
- Do not log presigned URLs, secret tokens, course files, raw student questions, or private teacher notes.
- Student HTTP responses must be tested for leakage of unapproved evidence and instructor-only content.
- Real lecture files are not allowed during prerelease testing; use synthetic demo PDF only.

## 6. Release checklist (not yet all completed)

1. GitHub Actions backend regression and Northflank unit tests pass on THIS branch.
2. Both containers build from the SAME commit and start without missing runtime variables.
3. API GET /health returns HTTP 200 and GET /health/db confirms Neon SELECT 1.
4. Neon application tables exist; API and worker can query ingest_jobs.
5. Direct PUT, complete, QUEUED, RUNNING and READY succeed using a synthetic PDF.
6. Authenticated faculty approval persists immutable signed evidence.
7. The student HTTP response includes only valid instructor-approved blocks; test raw network responses.
8. Verify job recovery, error retries, large PPTX and 367-page PDF memory/time budget before use in a real class.
9. Confirm beta invite authentication, six-character student join codes, retention/deletion policy and abuse limits before public homepage integration.

Known gaps: This branch does not add beta invite codes, join/expiry APIs, deletion APIs, object-storage CORS settings or student real-LLM quality evaluation. Service creation has not yet been performed in Northflank.

## 7. Rollback

Stop the worker and disable the public API port before reverting to a prior reviewed deployment. Keep the Neon DB and private bucket intact. Never delete or rewrite signed evidence merely because a container is redeployed.

Northflank references:
- https://northflank.com/docs/v1/application/build/build-code-from-a-git-repository
- https://northflank.com/docs/v1/application/build/build-with-a-dockerfile
- https://northflank.com/docs/v1/application/network/configure-ports
- https://northflank.com/docs/v1/application/observe/configure-health-checks

## Faculty beta from the homepage

Deploy the API branch containing `POST /v1/beta/faculty/session` before deploying its matching frontend. Keep the beta closed until the worker and private upload round trip have been verified. Configure `NIT_BETA_FACULTY_INVITES` as a runtime secret containing a JSON object mapping SHA-256 hex digests to existing active faculty user IDs. Generate one distinct code per instructor using Python's `secrets.token_urlsafe(32)` and send the raw code privately. The API exchanges a valid code for a 60-minute session. Removing a digest prevents new exchanges.

The faculty homepage uses the fixed Northflank API, direct private uploads, and the existing signed evidence approval endpoints. Student classroom entry remains a demo. To enable live faculty use, the API and worker need the private storage configuration above, `NIT_WORKER_BACKEND=northflank_poll`, and the API needs `NIT_REVIEW_SIGNING_KEY` and `NIT_CORS_ORIGINS` for the exact frontend deployment origin.
