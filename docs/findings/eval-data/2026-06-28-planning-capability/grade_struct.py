"""Deterministic structural grader — scores each model's task graph against
anvil's OWN parser/validator rules (llm_planner.py _validate_and_normalize +
_TASK_HEADING_RE, and the SYSTEM prompt's STRICT rules).

No judgment: every check here is mechanical and reproducible.
"""
import re, os, glob, json

OUT = os.path.dirname(os.path.abspath(__file__))

VALID_FEATURES = {
    "prdA-backlog": {f"F00{i}" for i in range(1, 9)},          # F001-F008
    "prdB-multiprd": {f"F00{i}" for i in range(1, 10)},        # F001-F009
}

TASK_HEADING_RE = re.compile(r"^###\s+(T\d{3,})\b[:]?\s*(.*)$", re.MULTILINE)
PRIORITIES = {"low", "medium", "high", "critical"}

def strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"): lines = lines[1:]
        if lines and lines[-1].startswith("```"): lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

def split_blocks(text):
    """Return list of (taskid, blocktext)."""
    matches = list(TASK_HEADING_RE.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        blocks.append((m.group(1), text[start:end]))
    return blocks

def field(block, name):
    m = re.search(rf"\*\*{name}:\*\*\s*(.*)", block)
    return m.group(1).strip() if m else None

def section_bullets(block, header):
    # bullets following a "**Header:**" line until next ** or ###
    m = re.search(rf"\*\*{re.escape(header)}:\*\*", block)
    if not m: return None
    tail = block[m.end():]
    # stop at next bold field header or next task
    stop = re.search(r"\n\*\*[A-Z][a-z].*?:\*\*|\n### ", tail)
    region = tail[:stop.start()] if stop else tail
    return [ln.strip() for ln in region.splitlines() if ln.strip().startswith(("-", "*"))]

def grade(pid, label, text):
    valid_feats = VALID_FEATURES[pid]
    raw = text
    text = strip_fences(text)
    r = {"prd": pid, "model": label}
    # parseable?
    has_header = text.lstrip().lower().startswith("## tasks") or ("## Tasks" in text)
    blocks = split_blocks(text)
    ids = [b[0] for b in blocks]
    r["parseable"] = bool(blocks)              # anvil requires >=1 ### TXXX
    r["has_tasks_header"] = bool(has_header)
    r["n_tasks"] = len(blocks)
    n = len(blocks) or 1

    # ID hygiene
    nums = [int(t[1:]) for t in ids]
    r["ids_zero_padded"] = all(re.fullmatch(r"T\d{3}", t) for t in ids)
    r["ids_unique"] = len(set(ids)) == len(ids)
    r["ids_sequential_no_skips"] = nums == list(range(1, len(nums)+1)) if nums else False

    # per-task field completeness
    feat_ok = pri_ok = acc_ok = ver_ok = vercmd_ok = files_ok = 0
    bad_feat_refs, dep_edges, all_ids = [], [], set(ids)
    for tid, blk in blocks:
        f = field(blk, "Feature")
        if f:
            refs = re.findall(r"F\d{3}", f)
            if refs and all(x in valid_feats for x in refs):
                feat_ok += 1
            else:
                bad_feat_refs.append((tid, f))
        p = field(blk, "Priority")
        if p and p.split()[0].lower() in PRIORITIES: pri_ok += 1
        if field(blk, "Likely files"): files_ok += 1
        acc = section_bullets(blk, "Acceptance criteria")
        if acc and len(acc) >= 1: acc_ok += 1
        ver = section_bullets(blk, "Verification")
        if ver and len(ver) >= 1:
            ver_ok += 1
            if any("`" in v for v in ver): vercmd_ok += 1
        d = field(blk, "Dependencies")
        if d:
            for dep in re.findall(r"T\d{3}", d):
                dep_edges.append((tid, dep))

    r["pct_feature_ref_valid"] = round(100*feat_ok/n)
    r["pct_priority_valid"] = round(100*pri_ok/n)
    r["pct_has_likely_files"] = round(100*files_ok/n)
    r["pct_has_acceptance"] = round(100*acc_ok/n)
    r["pct_has_verification"] = round(100*ver_ok/n)
    r["pct_verification_has_cmd"] = round(100*vercmd_ok/n)
    r["bad_feature_refs"] = bad_feat_refs[:6]

    # dependency integrity
    dangling = [(a,b) for (a,b) in dep_edges if b not in all_ids]
    selfdep = [(a,b) for (a,b) in dep_edges if a == b]
    r["n_dep_edges"] = len(dep_edges)
    r["dangling_deps"] = dangling[:8]
    r["self_deps"] = selfdep
    # cycle detection
    graph = {}
    for a,b in dep_edges:
        graph.setdefault(a, set()).add(b)
    WHITE, GRAY, BLACK = 0,1,2
    color = {}
    cyc = [False]
    def dfs(u):
        color[u] = GRAY
        for v in graph.get(u, ()):
            if color.get(v,WHITE)==GRAY: cyc[0]=True
            elif color.get(v,WHITE)==WHITE: dfs(v)
        color[u]=BLACK
    for u in list(all_ids):
        if color.get(u,WHITE)==WHITE: dfs(u)
    r["has_dep_cycle"] = cyc[0]

    # feature coverage: every PRD feature has >=1 task
    covered = set()
    for tid, blk in blocks:
        f = field(blk, "Feature") or ""
        covered |= set(re.findall(r"F\d{3}", f)) & valid_feats
    r["features_covered"] = f"{len(covered)}/{len(valid_feats)}"
    r["uncovered_features"] = sorted(valid_feats - covered)

    # composite structural score (0-100): mechanical correctness only
    checks = [
        r["parseable"], r["ids_zero_padded"], r["ids_unique"],
        r["ids_sequential_no_skips"], not r["has_dep_cycle"],
        not dangling, not selfdep, not bad_feat_refs,
        r["pct_has_acceptance"]==100, r["pct_verification_has_cmd"]==100,
        r["pct_feature_ref_valid"]==100, r["pct_priority_valid"]==100,
        len(covered)==len(valid_feats),
    ]
    r["structural_score"] = round(100*sum(bool(c) for c in checks)/len(checks))
    return r

rows = []
for fn in sorted(glob.glob(f"{OUT}/out_*.md")):
    base = os.path.basename(fn)[4:-3]   # strip out_ and .md
    pid, label = base.split("__")
    with open(fn, encoding="utf-8") as f:
        rows.append(grade(pid, label, f.read()))

with open(f"{OUT}/grade_struct.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=2)

# print compact scoreboard
cols = ["prd","model","n_tasks","structural_score","ids_sequential_no_skips",
        "has_dep_cycle","dangling_deps","pct_has_acceptance","pct_verification_has_cmd",
        "pct_feature_ref_valid","features_covered"]
print(f"{'prd':14} {'model':9} {'tasks':5} {'struct%':7} {'seqIDs':6} {'cycle':5} {'dangl':5} {'acc%':5} {'vcmd%':5} {'feat%':5} {'cover':6}")
for r in rows:
    print(f"{r['prd']:14} {r['model']:9} {r['n_tasks']:5} {r['structural_score']:7} "
          f"{str(r['ids_sequential_no_skips']):6} {str(r['has_dep_cycle']):5} "
          f"{len(r['dangling_deps']):5} {r['pct_has_acceptance']:5} {r['pct_verification_has_cmd']:5} "
          f"{r['pct_feature_ref_valid']:5} {r['features_covered']:6}")
print("\nFull JSON -> grade_struct.json")
