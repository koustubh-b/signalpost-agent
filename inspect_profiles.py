import json

path = "profiles.jsonl"
total = 0
errors = 0
has_financials = 0
bankrupt_count = 0
missing_name = 0
sample_shown = 0

with open(path, encoding="utf-8") as f:
    for line in f:
        total += 1
        rec = json.loads(line)
        if "error" in rec:
            errors += 1
            continue
        facts = rec.get("facts", {})
        if facts.get("financials", {}).get("value"):
            has_financials += 1
        if facts.get("status", {}).get("value", {}).get("bankrupt"):
            bankrupt_count += 1
        if not facts.get("name", {}).get("value"):
            missing_name += 1
        if sample_shown < 3 and "error" not in rec:
            print(f"--- sample profile: {rec['organisasjonsnummer']} ---")
            print(f"  name: {facts.get('name', {}).get('value')}")
            print(f"  status: {facts.get('status', {}).get('value')}")
            print(f"  financials present: {bool(facts.get('financials', {}).get('value'))}")
            sample_shown += 1

print("\n=== SUMMARY ===")
print(f"Total lines: {total}")
print(f"Hard errors (lookup failed entirely): {errors}")
print(f"Profiles with a name: {total - errors - missing_name}")
print(f"Profiles with financials populated: {has_financials} ({100*has_financials/max(total-errors,1):.1f}%)")
print(f"Profiles flagged bankrupt: {bankrupt_count}")