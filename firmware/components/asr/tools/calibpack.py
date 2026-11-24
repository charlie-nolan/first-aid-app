"""reject_calib.json (fasr calibrate) -> ASRC chunk: "RJC1" + 26 float64 (mean[8] std[8] weights[8] bias threshold)."""
import json, struct, sys
c = json.loads(open(sys.argv[1]).read())
assert len(c["mean"]) == len(c["std"]) == len(c["weights"]) == 8, "reject_features has 8 inputs"
open(sys.argv[2], "wb").write(b"RJC1" + struct.pack("<26d", *(c["mean"] + c["std"] + c["weights"] + [c["bias"], c["threshold"]])))
print(sys.argv[2], "ok")
