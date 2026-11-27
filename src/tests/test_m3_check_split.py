import json
m = json.load(open("data/m3/split.json", encoding="utf-8"))["members"]["Nguyen"]
bad = {"2602.04979","2602.05158","2602.05316","2602.06165","2602.09175"}
print("test:", m["test"], "valid:", m["valid"])
print("bad trong test/valid:", bad & {a for p in ("test","valid") for v in m[p].values() for a in v})