# Bake-off metrics (letters hidden)

Built during the Meta Global AI Developer Hackathon (new work).

One run per variant per video. Steps per scored param lists the six scored parameters in canonical order (Hook, Retention, Shareability, Audio, Trend, Visual). Tokens out include the hidden reasoning tokens (billed as output). Truncated fields = text that stops mid-sentence or empty required fields, plus replies that ended at the token limit.

| Video | Letter | Valid JSON | Truncated fields | Steps per scored param | Scored params < 3 steps | Avg words / problem | Transcript quotes | Advisory with ready wording | Repeated closings | Overall | Latency (s) | Tokens in | Tokens out (incl. reasoning) | of which reasoning | Cost (USD) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dance | X | yes | 0 | 6, 2, 3, 4, 2, 2 | 3 | 38.4 | 27 | 3/4 | 4 | 66 | 85.7 | 22,617 | 22,564 | 14,494 | 0.1242 |
| dance | Y | yes | 0 | 5, 5, 1, 4, 3, 5 | 1 | 35.7 | 12 | 4/4 | 0 | 63 | 267.8 | 32,781 | 38,182 | 26,561 | 0.8948 |
| dance | Z | yes | 0 | 3, 3, 2, 2, 3, 1 | 3 | 19.9 | 0 | 1/4 | 0 | 66 | 50.4 | 20,341 | 12,035 | 5,962 | 0.0766 |
| reel | X | yes | 0 | 4, 3, 4, 3, 4, 3 | 0 | 33.6 | 39 | 4/4 | 0 | 66 | 54.7 | 24,714 | 13,449 | 5,968 | 0.0881 |
| reel | Y | yes | 0 | 3, 3, 3, 3, 3, 3 | 0 | 20.8 | 28 | 4/4 | 0 | 67 | 77.8 | 22,438 | 21,270 | 15,408 | 0.1184 |
| reel | Z | yes | 0 | 4, 6, 4, 5, 5, 6 | 0 | 38.7 | 33 | 4/4 | 0 | 63 | 101.8 | 24,714 | 20,932 | 12,492 | 0.5175 |
| saree | X | yes | 0 | 5, 5, 2, 5, 4, 6 | 1 | 38.5 | 16 | 3/4 | 0 | 68 | 143.3 | 24,177 | 25,921 | 17,348 | 0.6151 |
| saree | Y | yes | 0 | 4, 3, 4, 3, 3, 2 | 1 | 34.3 | 39 | 3/4 | 0 | 68 | 64.8 | 24,177 | 15,417 | 8,254 | 0.0957 |
| saree | Z | yes | 0 | 3, 2, 2, 3, 2, 2 | 4 | 22.5 | 26 | 4/4 | 0 | 70 | 58.9 | 21,901 | 14,879 | 8,741 | 0.0906 |
