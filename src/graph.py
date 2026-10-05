"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
# KG_ONTOLOGY=hint rebuilds the suggested ontology (baseline for ket_qua_benchmark_kg.hint.txt); default = own.
ONTOLOGY = os.getenv("KG_ONTOLOGY", "own").strip().lower()
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    target = normalize(name or "")
    if not target:
        return None
    by_normalized = {normalize(k): k for k in known if k}
    if target in by_normalized:
        return by_normalized[target]
    close = difflib.get_close_matches(target, list(by_normalized), n=1, cutoff=0.8)
    return by_normalized[close[0]] if close else None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# OWN ontology helpers: substance synonyms, quantities, quantity thresholds in the law
# ----------------------------------------------------------------------------------------------

# Substances BLHS names explicitly in its quantity lists; anything else falls into "other drugs, solid".
BLHS_LISTED = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11"]
OTHER_SOLID = "Chất ma túy khác (thể rắn)"
OTHER_LIQUID = "Chất ma túy khác (thể lỏng)"
# Street / journalistic names -> canonical name (E3: one real substance = one node).
SUBSTANCE_ALIASES = {
    "heroin": "Heroine", "hêrôin": "Heroine", "cocain": "Cocaine", "côcain": "Cocaine",
    "methamphetamin": "Methamphetamine", "metamphetamine": "Methamphetamine", "ma túy đá": "Methamphetamine",
    "ma tuý đá": "Methamphetamine", "hồng phiến": "Methamphetamine", "meth": "Methamphetamine",
    "amphetamin": "Amphetamine", "thuốc lắc": "MDMA", "ecstasy": "MDMA", "ketamin": "Ketamine",
    "ke": "Ketamine", "cỏ mỹ": "XLR-11", "nhựa cần sa": "cần sa", "cỏ": "cần sa", "marijuana": "cần sa",
    "nhựa thuốc phiện": "thuốc phiện", "cao côca": "côca", "lá côca": "côca",
}

def normalize_substance(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name).strip().lower())

def canonical_substance(name: str) -> str:
    """'ma túy đá (methamphetamine)' -> 'Methamphetamine'; unknown names are kept, just trimmed."""
    name = unicodedata.normalize("NFC", name or "").strip()
    candidates = [re.sub(r"\(.*?\)", "", name).strip(), *re.findall(r"\((.*?)\)", name), name]
    for candidate in candidates:
        key = normalize_substance(candidate)
        if key in SUBSTANCE_ALIASES:
            return SUBSTANCE_ALIASES[key]
        linked = link_entity(candidate, SUBSTANCES, normalize_substance)
        if linked:
            return linked
    return candidates[0] or name

def legal_class(substance: str) -> str:
    """Which quantity row of BLHS applies to a canonical substance."""
    plant_based = ["cần sa", "thuốc phiện", "côca"]   # BLHS rows by form (resin, leaves, pods)
    return substance if substance in BLHS_LISTED + plant_based else OTHER_SOLID

_UNIT_G = {"g": 1, "gam": 1, "gram": 1, "gr": 1, "kg": 1000, "kilôgam": 1000, "kilogam": 1000, "kilogram": 1000,
           "tấn": 1_000_000, "mililít": 1, "ml": 1}

def _number(raw: str) -> float:
    """Vietnamese numerals: '9,6' -> 9.6, '1.000' -> 1000, '05' -> 5."""
    if "," in raw:
        return float(raw.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        return float(raw.replace(".", ""))
    return float(raw)

def to_grams(text: str | float | int | None) -> float | None:
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None
    match = re.search(r"(\d+(?:[.,]\d+)*)\s*(kilôgam|kilogam|kilogram|kg|tấn|gam|gram|gr|g)(?![a-zà-ỹ])",
                      (text or "").lower())
    return _number(match.group(1)) * _UNIT_G[match.group(2)] if match else None

POINT_LINE = re.compile(r"^([a-zđ])\)\s*(.+)$", re.MULTILINE)
THRESHOLD = re.compile(r"(khối lượng|thể tích)\s+(?:từ\s+)?(\d+(?:[.,]\d+)?)\s*(gam|kilôgam|mililít)"
                       r"(?:\s+đến dưới\s+(\d+(?:[.,]\d+)?)\s*(gam|kilôgam|mililít)|\s+trở lên)")

def parse_thresholds(clause_text: str) -> list[dict]:
    """One row per (điểm, substance): 'MDMA ... có khối lượng 100 gam trở lên' -> min_g=100, max_g=None."""
    rows = []
    for point, line in POINT_LINE.findall(clause_text):
        match = THRESHOLD.search(line)
        if not match:
            continue
        form = line[:match.start()].strip().removesuffix("có").strip(" ,;")
        substances = find_substances(form)
        if not substances and "chất ma túy khác" in form:
            substances = [OTHER_LIQUID if match.group(1) == "thể tích" else OTHER_SOLID]
        low = _number(match.group(2)) * _UNIT_G[match.group(3)]
        high = _number(match.group(4)) * _UNIT_G[match.group(5)] if match.group(4) else None
        for substance in substances:
            rows.append({"point": point, "substance": substance, "form": form[:120],
                         "min_g": low, "max_g": high, "unit": "ml" if match.group(1) == "thể tích" else "g"})
    return rows

# ----------------------------------------------------------------------------------------------
# HINT — suggested ontology: extraction helpers
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 8 năm tù"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases

# ----------------------------------------------------------------------------------------------
# OWN ontology: extraction (differences from the HINT are marked with ★)
# ----------------------------------------------------------------------------------------------

def parse_law_article_own(doc: Document) -> dict[str, Any]:
    """HINT parser + ★ quantity thresholds per điểm (THRESHOLD edges instead of bare MENTIONS)."""
    article = parse_law_article(doc)
    for clause in article["clauses"]:
        clause["thresholds"] = parse_thresholds(clause["text"])
        with_threshold = {t["substance"] for t in clause["thresholds"]}
        clause["substances"] = [s for s in clause["substances"] if s not in with_threshold]
    return article

OWN_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài, không suy đoán. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ vận chuyển 9,6kg MDMA qua sân bay Nội Bài",
  "summary": "1-2 câu tóm tắt: ai, làm gì, chất gì, bao nhiêu, kết quả",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "stage": "giai đoạn tố tụng mới nhất trong bài: bắt giữ|khởi tố|truy tố|xét xử sơ thẩm|xét xử phúc thẩm|khác",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH; hành vi 'tổ chức sử dụng' cũng phải map vào đây"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp (ma túy đá = Methamphetamine, thuốc lắc = MDMA, ketamin = Ketamine)",
                   "amount": "khối lượng nguyên văn trong bài nếu có, ví dụ: hơn 9,6kg",
                   "amount_g": "khối lượng quy ra gam (số), null nếu bài không nêu"}}],
  "people": [{{"name": "họ tên đầy đủ, KHÔNG kèm biệt danh hay tuổi", "aliases": ["biệt danh, ví dụ: Hoàng Nato"],
               "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 36 tháng tù"}}]
}}]}}
Một bài có thể có nhiều vụ việc tách biệt. Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""

def _person_name(name: str) -> str:
    """'Dương Minh Tuấn (Hoàng Nato)' -> 'Dương Minh Tuấn'; collapse spaces, NFC (E3: duplicate people)."""
    name = re.sub(r"\(.*?\)|,.*$", "", unicodedata.normalize("NFC", name or ""))
    return re.sub(r"\s+", " ", name).strip()

def extract_news_cases_own(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    prompt = OWN_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        substances = {}
        for s in case.get("substances", []):
            if not s.get("name"):
                continue
            name = canonical_substance(s["name"])
            # Parse the quoted amount in code first (deterministic); the LLM's own conversion is the fallback.
            raw = s.get("amount_g")
            grams = to_grams(s.get("amount") or "")
            if grams is None and isinstance(raw, (int, float)):
                grams = to_grams(raw)
            elif grams is None and isinstance(raw, str) and re.fullmatch(r"\d+(?:[.,]\d+)?", raw.strip()):
                grams = to_grams(raw.strip() + " g")
            substances[name] = {"name": name, "amount": s.get("amount") or "", "amount_g": grams,
                                "legal_class": legal_class(name)}
        case["substances"] = list(substances.values())
        people = {}
        for p in case.get("people", []):
            name = _person_name(p.get("name", ""))
            if len(name) < 3:
                continue
            aliases = [a.strip() for a in p.get("aliases") or [] if a and a.strip() and a.strip() != name]
            aliases += re.findall(r"\((.*?)\)", p.get("name", ""))
            people[name] = {**p, "name": name, "aliases": sorted(set(aliases)),
                            "charge": link_entity(p.get("charge") or "", known_crimes) or "",
                            "sentence": p.get("sentence") or "", "role": p.get("role") or ""}
        case["people"] = list(people.values())
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- OWN ontology: writes

    def own_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "id"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article_own(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            FOREACH (t IN clause.thresholds | MERGE (sub:Substance {name: t.substance})
                MERGE (cl)-[r:THRESHOLD {point: t.point}]->(sub)
                SET r.min_g = t.min_g, r.max_g = t.max_g, r.unit = t.unit, r.form = t.form)
            """,
            **article,
        )

    def add_news_case_own(self, case: dict, doc: Document, index: int) -> None:
        self.run(
            """
            MERGE (k:Case {id: $id})
              SET k.name = $name, k.summary = $summary, k.date = $date, k.stage = $stage,
                  k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount, r.amount_g = s.amount_g, r.legal_class = s.legal_class)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = [a IN coalesce(person.aliases, []) WHERE NOT a IN p.aliases] + p.aliases
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            id=f"{doc.id}#{index}", name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), stage=case.get("stage", ""),
            location=case.get("location", "") or "", charges=case.get("charges", []),
            people=case.get("people", []), substances=case.get("substances", []),
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    def link_falls_under(self) -> None:
        """★ Derived edge: put each (case, substance, amount) into the clause whose quantity range contains it."""
        self.run(
            """
            MATCH (k:Case)-[i:INVOLVES]->(:Substance)
            WHERE i.amount_g IS NOT NULL
            MATCH (k)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(:Article)-[:HAS_CLAUSE]->(cl:Clause)
                  -[t:THRESHOLD]->(cls:Substance {name: i.legal_class})
            WHERE t.unit = 'g' AND i.amount_g >= t.min_g AND (t.max_g IS NULL OR i.amount_g < t.max_g)
            MERGE (k)-[f:FALLS_UNDER {substance: endNode(i).name, point: t.point}]->(cl)
              SET f.amount_g = i.amount_g, f.amount = i.amount, f.min_g = t.min_g, f.max_g = t.max_g, f.form = t.form
            """
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: seeds + 1 hop, then the legal basis of every case reached."""
        if ONTOLOGY == "hint":
            return self._context_hint(question, doc_ids, max_facts)
        return self._context_own(question, doc_ids, max_facts)

    def _clause_facts(self, article_ids: list[str], case_ids: list[str], substances: list[str]) -> list[str]:
        """HINT rule: clause 1 + clauses that MENTION a substance of the case / of the question."""
        rows = self.run(
            """
            MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
            WHERE (a.id IN $aids AND (cl.number = 1 OR EXISTS {
                     MATCH (cl)-[:MENTIONS]->(s:Substance) WHERE s.name IN $subs }))
               OR EXISTS {
                     MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a)
                     WHERE elementId(k) IN $cids
                       AND (cl.number = 1 OR EXISTS { MATCH (k)-[:INVOLVES]->(:Substance)<-[:MENTIONS]-(cl) }) }
            RETURN a.id AS id, a.title AS title, cl.number AS number, cl.text AS text
            ORDER BY id, number
            """,
            aids=article_ids, cids=case_ids, subs=substances,
        )
        return [f"[{r['id']} - {r['title']}] khoản {r['number']}: {r['text']}" for r in rows]

    def _context_hint(self, question: str, doc_ids: list[str], max_facts: int) -> list[str]:
        seed_ids, facts = self.seed_facts(question, doc_ids)
        cases = self.run(
            """
            MATCH (k:Case)
            WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
            RETURN elementId(k) AS id, k.name AS name, k.summary AS summary
            """,
            ids=seed_ids,
        )
        case_facts = [f"Vụ việc '{c['name']}': {c['summary']}" for c in cases]
        article_ids = [f"Điều {n} BLHS" for n in re.findall(r"[Đđ]iều (\d+)", question)]
        clause_facts = self._clause_facts(article_ids, [c["id"] for c in cases], find_substances(question))
        return list(dict.fromkeys(case_facts + clause_facts + facts))[:max_facts]

    def _context_own(self, question: str, doc_ids: list[str], max_facts: int) -> list[str]:
        # 1. Seeds (ontology-independent). Clause neighbourhoods are skipped: clause text is added explicitly below.
        seed_ids, seed_facts = self.seed_facts(question, doc_ids, skip_labels=("Clause",), limit=30)
        q_substances = sorted({canonical_substance(s) for s in find_substances(question)} |
                              {v for k, v in SUBSTANCE_ALIASES.items()
                               if len(k) > 3 and re.search(rf"\b{re.escape(k)}\b", question.lower())})

        # 2. Cases: seeds / next to a seed / ★ involving a substance named in the question (aggregation).
        #    Ranked by how many seeds touch them so a person named in the question wins over a shared Location.
        cases = self.run(
            """
            MATCH (k:Case)
            OPTIONAL MATCH (k)--(s) WHERE elementId(s) IN $ids
            WITH k, count(s) + CASE WHEN elementId(k) IN $ids THEN 2 ELSE 0 END AS hits,
                 EXISTS { MATCH (k)-[:INVOLVES]->(x:Substance) WHERE x.name IN $subs } AS by_substance
            WHERE hits > 0 OR by_substance
            OPTIONAL MATCH (k)-[:LOCATED_IN]->(l:Location)
            OPTIONAL MATCH (k)-[:CHARGED_WITH]->(c:Crime)
            OPTIONAL MATCH (k)-[i:INVOLVES]->(sub:Substance)
            OPTIONAL MATCH (p:Person)-[r:INVOLVED_IN]->(k)
            RETURN elementId(k) AS id, k.name AS name, k.summary AS summary, k.date AS date, k.stage AS stage,
                   k.doc_id AS doc_id, hits, collect(DISTINCT l.name) AS locations, collect(DISTINCT c.name) AS crimes,
                   collect(DISTINCT sub.name + CASE WHEN i.amount <> '' THEN ' (' + i.amount + ')' ELSE '' END) AS subs,
                   collect(DISTINCT p.name + CASE WHEN size(p.aliases) > 0
                                                  THEN ' (biệt danh ' + reduce(s = '', a IN p.aliases | s + a + ' ') + ')'
                                                  ELSE '' END
                                    + ': ' + coalesce(r.role, '')
                                    + CASE WHEN r.charge <> '' THEN ', tội ' + r.charge ELSE '' END
                                    + CASE WHEN r.sentence <> '' THEN ', án ' + r.sentence ELSE '' END) AS people
            ORDER BY hits DESC LIMIT 10
            """,
            ids=seed_ids, subs=q_substances,
        )
        case_ids = [c["id"] for c in cases]
        facts = []
        for c in cases:
            facts.append(
                f"Vụ việc '{c['name']}' [{c['doc_id']}; {c['date'] or 'không rõ ngày'}; "
                f"{', '.join(c['locations']) or 'không rõ nơi'}; giai đoạn: {c['stage'] or 'không rõ'}]: {c['summary']}"
                f" | Tội danh: {', '.join(c['crimes']) or 'chưa nối được tội danh'}"
                f" | Chất: {', '.join(c['subs']) or 'không rõ'}"
                f" | Người: {'; '.join(c['people']) or 'không rõ'}")

        # 3. ★ Quantity-based clause chosen in the graph: Case -FALLS_UNDER-> Clause (multi-hop done at build time).
        for r in self.run(
            """
            MATCH (k:Case)-[f:FALLS_UNDER]->(cl:Clause)<-[:HAS_CLAUSE]-(a:Article)
            WHERE elementId(k) IN $cids
            RETURN k.name AS case, f.substance AS substance, f.amount AS amount, f.amount_g AS grams,
                   f.point AS point, f.min_g AS min_g, f.max_g AS max_g, f.form AS form,
                   a.id AS article, a.title AS title, cl.number AS number, cl.penalty AS penalty
            """,
            cids=case_ids,
        ):
            limit = f"từ {r['min_g']:g} g" + (f" đến dưới {r['max_g']:g} g" if r["max_g"] is not None else " trở lên")
            facts.append(f"Khung áp dụng theo khối lượng: vụ '{r['case']}' có {r['substance']} {r['amount'] or ''} "
                         f"(≈ {r['grams']:g} g) thuộc [{r['article']} - {r['title']}] khoản {r['number']} điểm {r['point']} "
                         f"({r['form']}: {limit}) → {r['penalty']}")

        # 4. Bridge to the law KB: articles reached through Crime, named in the question, or retrieved by vector.
        named = [f"Điều {n} BLHS" for n in re.findall(r"[Đđ]iều (\d+)", question)]
        articles = self.run(
            """
            MATCH (a:Article)
            WHERE a.id IN $named OR elementId(a) IN $ids
               OR EXISTS { MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a) WHERE elementId(k) IN $cids }
            MATCH (a)-[:HAS_CLAUSE]->(cl:Clause)
            WITH a, cl ORDER BY cl.number
            RETURN a.id AS id, a.title AS title,
                   collect({number: cl.number, penalty: cl.penalty, text: cl.text}) AS clauses
            """,
            named=named, ids=seed_ids, cids=case_ids,
        )
        for a in articles:
            ladder = "; ".join(f"khoản {c['number']}: {c['penalty']}" for c in a["clauses"] if c["penalty"])
            if ladder:   # ★ every penalty band of the article, so "tối đa" questions see the top band (E2)
                facts.append(f"[{a['id']} - {a['title']}] các khung hình phạt: {ladder}")
            first = a["clauses"][0]["text"] if a["clauses"] else ""
            facts.append(f"[{a['id']} - {a['title']}] khoản 1: {first[:600]}")

        # 5. Quantity rows of the named articles for the substances named in the question.
        for r in self.run(
            """
            MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)-[t:THRESHOLD]->(s:Substance)
            WHERE a.id IN $named AND s.name IN $subs
            RETURN a.id AS id, cl.number AS number, cl.penalty AS penalty, t.point AS point,
                   s.name AS substance, t.min_g AS min_g, t.max_g AS max_g
            ORDER BY id, number
            """,
            named=named, subs=[legal_class(s) for s in q_substances],
        ):
            limit = f"từ {r['min_g']:g} g" + (f" đến dưới {r['max_g']:g} g" if r["max_g"] is not None else " trở lên")
            facts.append(f"[{r['id']}] khoản {r['number']} điểm {r['point']}: {r['substance']} {limit} → {r['penalty']}")

        return list(dict.fromkeys(facts + seed_facts))[:max_facts]

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    json_llm = lambda prompt: llm_fn(prompt, json_mode=True)   # noqa: E731
    if ONTOLOGY == "hint":
        graph.suggested_constraints()
        articles = [parse_law_article(d) for d in law_docs]
        for article in articles:
            graph.add_law_article(article)
        crimes = [a["crime"] for a in articles if a["crime"]]
        for doc in news_docs:
            for case in extract_news_cases(doc, json_llm, crimes):
                graph.add_news_case(case, doc)
        return

    graph.own_constraints()
    articles = [parse_law_article_own(d) for d in law_docs]       # regex: deterministic and free
    for article in articles:
        graph.add_law_article_own(article)
    crimes = [a["crime"] for a in articles if a["crime"]]
    for doc in news_docs:                                         # LLM -> JSON -> link_entity
        for index, case in enumerate(extract_news_cases_own(doc, json_llm, crimes)):
            graph.add_news_case_own(case, doc, index)
    graph.link_falls_under()

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)
        doc_ids = list(dict.fromkeys(c["metadata"].get("doc_id") for c in chunks if c["metadata"].get("doc_id")))
        facts = self.graph.context(question, doc_ids)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {fact}" for fact in facts) or "(không có)",
            chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)),
            question=question,
        )
        return self.llm_fn(prompt)
