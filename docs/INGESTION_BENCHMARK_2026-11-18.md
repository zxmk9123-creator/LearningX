# Local native extraction benchmark — 2026-11-18

Three user-supplied original lesson files were processed with adaptive ingestion.
The source files and extracted text are intentionally excluded from the repository.
Timings and peak child-process RSS are measurements on this audit machine, not
hosted latency or an accuracy score. Candidates still require faculty approval.

| File | Size (bytes) | Pages | Blocks | Candidates | OCR jobs | Vision jobs | Seconds | Peak RSS (KiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| PDF A | 1,379,737 | 37 | 84 | 34 | 7 | 20 | 0.66 | 67,340 |
| PPTX A | 54,074 | 5 | 11 | 1 | 0 | 0 | 0.31 | 36,924 |
| PDF B | 35,395,002 | 367 | 1,843 | 1,417 | 33 | 120 | 3.76 | 191,656 |

SHA-256: PDF A `751bd3b0e7aa9e8a5e82caacf33cf603349b139286c311f90d223c5e523c0a76`;
PPTX A `38542ece21f8cdcd4c599030ca18383da4e231a0d0864f2d1c45f9cfdc38f29d`;
PDF B `1d69bf71ce861f06ad35d725f86d02b22a3da2d06fea6fec46c464df70d2f053`.

All three native parser runs exited successfully. No original-file OCR, Vision,
semantic synthesis, hosted upload or faculty approval is included in these timings.
The 367-page file approaches the memory budget of the 256 MB free worker before
worker/DB overhead; hosted suitability requires measurement on the actual deployment.
The original 32.9 MB / 13-audio PPTX acceptance case is still unavailable here.
