# Signalpost 100-Company Smoke Test

## Result

- Inputs: 100
- Terminal envelopes: 100
- Unique organisation numbers: 100
- Available: 100
- Other states: 0
- Profile validation problems: 0

## Validation Commands

`py -3 scripts/validate_smoke.py sample_orgnrs_100_final.txt output/profiles_100_final.jsonl`

`py -3 scripts/validate_profiles.py output/profiles_100_final.jsonl`

## Validation Output

PASS: 100 inputs -> 100 terminal envelopes
Unique organisation numbers: 100

checked 100 profiles -> 0 problem(s)

## Environment

- Python: 3.x
- Workers: 8
- Input: 100 organisation numbers from the official Signalpost 2025 company universe
- Output: JSONL terminal result envelopes
