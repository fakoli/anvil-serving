"""Execute the packaged Pi owner adapter against a synthetic pinned-owner API."""
from pathlib import Path
import re
import shutil
import subprocess
import pytest


def test_packaged_owner_adapter_behavior(tmp_path):
    node=shutil.which("node")
    if not node: pytest.skip("Node is required for the packaged TypeScript behavior gate")
    patch=Path(__file__).parents[2]/"anvil_serving/_pi_web_bridge/0.10.0-host-bridge.patch"
    blocks=patch.read_text().split("diff --git ")
    for packaged,name in [("lib/native-agents-owner.ts","owner.ts"),("lib/native-agents-owner-auth.ts","auth.ts")]:
        block=next(b for b in blocks if b.startswith("a/"+packaged+" "))
        source="\n".join(line[1:] for line in block.splitlines() if line.startswith("+") and not line.startswith("+++"))+"\n"
        if name=="owner.ts":
            source=source.replace('"./native-agents-owner-auth"','"./auth.ts"')
            source=re.sub(r'import \{ (.*?) \} from "(?!node:|\./auth\.ts)[^"]+";',lambda m:'import { '+m[1]+' } from "./owner-stub.ts";',source)
        (tmp_path/name).write_text(source)
    fixture=Path(__file__).parent/"fixtures/owner.test.mjs"
    shutil.copyfile(fixture,tmp_path/fixture.name)
    shutil.copyfile(fixture.with_name("owner-stub.ts"),tmp_path/"owner-stub.ts")
    result=subprocess.run([node,"--experimental-strip-types","--test",fixture.name],cwd=tmp_path,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
