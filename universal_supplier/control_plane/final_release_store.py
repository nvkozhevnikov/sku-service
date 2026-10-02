"""Immutable, read-only accepted release view; not persisted SQL decisions."""
import hashlib
import json
from pathlib import Path

class FinalReleaseStore:
    def __init__(self, directory, manifest_sha):
        self.directory=Path(directory).resolve()
        if len(manifest_sha)!=64:
            raise ValueError("Pinned final release manifest SHA required")
        body=(self.directory/"SHA256_MANIFEST.json").read_bytes()
        if hashlib.sha256(body).hexdigest()!=manifest_sha:
            raise ValueError("Final release manifest changed")
        self.manifest=json.loads(body)
        self.freeze=json.loads(self.file("FREEZE_MANIFEST.json"))
        path=self.directory/"accepted/MATCHING_ACCEPTED.json"
        accepted=path.read_bytes()
        if hashlib.sha256(accepted).hexdigest()!=self.freeze["input_sha256"]["MATCHING_ACCEPTED.json"]:
            raise ValueError("Accepted release decisions changed")
        self.rows=json.loads(accepted)["rows"]

    def file(self, name):
        if name not in self.manifest or Path(name).name!=name:
            raise ValueError("File outside release allowlist")
        path=(self.directory/name).resolve()
        if path.parent!=self.directory:
            raise ValueError("Release artifact escaped directory")
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=self.manifest[name]["sha256"]:
            raise ValueError("Release artifact SHA mismatch")
        return body

    def page(self, classification="", supplier="", page=1):
        if classification not in {"","EXISTING_CONFIRMED","READY_TO_CREATE_FULL","REVIEW","CONFLICT"} or page<1:
            raise ValueError("Invalid release filter")
        rows=[r for r in self.rows if (not classification or r["classification"]==classification)
              and (not supplier or r["source"]==supplier)]
        return {"rows":rows[(page-1)*50:page*50],"total":len(rows),"page":page,"pages":max(1,(len(rows)+49)//50),
                "summary":self.freeze["summary"],"classification":classification,"supplier":supplier,
                "files":[n for n in self.manifest if n.endswith((".xml",".csv",".xlsx"))]}
