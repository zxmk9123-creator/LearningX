# Optional OCR / Vision worker

Native extraction remains the default. Optional stages run before artifact publication,
with independent job limits and deadlines. A failed or timed-out stage preserves the
native output; OCR and model interpretations remain unsigned and cannot become
student coaching evidence through this stage.

Build the optional worker image with `docker build --build-arg NIT_DEEP_RUNTIME=1 backend`.
This installs Korean/English Tesseract data, LibreOffice slide rendering, fonts and the
Gemini SDK. The default image stays unchanged in runtime capabilities. The optional
image is also built and dependency-checked by the container CI matrix; local
Docker validation is unavailable in the audit environment.

| Worker variable | Default | Limit / effect |
| --- | --- | --- |
| NIT_OCR_ENABLED | 0 | Set 1 to run queued PDF OCR |
| NIT_OCR_LANGUAGE | kor+eng | Must exist in Tesseract language data |
| NIT_OCR_MAX_JOBS | 3 | 1–20 pages per uploaded document |
| NIT_OCR_TIMEOUT_SECONDS | 120 | 1–300 seconds per stage |
| NIT_VISION_ENABLED | 0 | Set 1 to run queued image/diagram extraction |
| GEMINI_API_KEY | unset | Required only when Vision is enabled |
| NIT_MODEL | engine default | Provider model for optional Vision |
| NIT_VISION_MAX_JOBS | 3 | 1–20 jobs per uploaded document |
| NIT_VISION_TIMEOUT_SECONDS | 120 | 1–300 seconds per stage |

Execution coverage records NOT_RUN, NOT_REQUIRED, UNAVAILABLE, FAILED, PARTIAL or
COMPLETE with counts. COMPLETE means scheduled extraction jobs returned, not that
the content is correct. Vision has visual_semantics_verified=false even on completion.
Remaining jobs beyond the cap are reported; automatic continuation is not implemented.
Semantic synthesis, audio transcription and reviewed OCR/AI promotion are not enabled.

Do not enable deep stages on the existing 256 MB free worker without a separate
memory/load check. The 367-page native benchmark alone peaked near 187 MiB; OCR,
rendering and API/worker overhead need additional headroom. No paid resources are
created by this change. Provider calls may incur cost only after an operator opts in.

Tests cover opt-in, missing provider, capped completion, report hash binding,
timeout rollback and a real English scanned-PDF Tesseract extraction. Vision provider
behavior is simulated in tests; real provider execution and Korean OCR remain unverified.
