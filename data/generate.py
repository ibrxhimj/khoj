"""Generate a synthetic Bangalore residential-property knowledge graph.

Everything here is fictional: developer brands, companies, people, case
numbers and RERA numbers are invented. Only locality and village names are
real geography. Do not replace these with real companies: pairing real names
with invented litigation would be defamatory.

The background data is random (seeded, so it is reproducible). On top of it we
plant four "stories" that only a graph can surface:

  1. Phoenix developer  - a spotless new launch whose directors ran a developer
                          that went insolvent and left two projects stalled.
  2. Disputed land      - the plot's previous owner is in a pending title suit.
  3. Contractor contagion - a premium project's contractor is behind on three
                          other projects and has a pending cheque-bounce case.
  4. Key person         - one director links six companies across four brands,
                          two of them failed (exposed by betweenness centrality).

Run:  python data/generate.py            -> writes data/seed.json
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

SEED = 42
SEED_TS = int(datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp())
OUT = Path(__file__).resolve().parent / "seed.json"

rng = random.Random(SEED)
nodes: dict[str, dict] = {}
edges: list[dict] = []
_counters: dict[str, int] = {}


# --------------------------------------------------------------------------- helpers
def nid(prefix: str) -> str:
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f"{prefix}-{_counters[prefix]:03d}"


def add_node(kind: str, node_id: str | None = None, **props) -> str:
    prefix = {
        "Group": "GRP", "Company": "CMP", "Person": "PER", "Project": "PRJ",
        "Locality": "LOC", "LandParcel": "LND", "Approval": "APR",
        "Complaint": "CPL", "CourtCase": "CRT", "Contractor": "CON", "Lender": "LEN",
    }[kind]
    node_id = node_id or nid(prefix)
    assert node_id not in nodes, node_id
    nodes[node_id] = {"id": node_id, "kind": kind, "created_at": SEED_TS, **props}
    return node_id


def add_edge(rel: str, src: str, dst: str, **props) -> None:
    edges.append({"type": rel, "src": src, "dst": dst, **props})


# --------------------------------------------------------------------------- reference data
LOCALITIES = {
    # name: (zone, planning authority, base rate INR/sqft, villages)
    "Whitefield": ("East", "BBMP", 9500, ["Hagadur", "Immadihalli", "Kundalahalli"]),
    "Sarjapur Road": ("South-East", "BBMP", 9000, ["Dommasandra", "Kodathi", "Doddakannelli"]),
    "Varthur": ("East", "BBMP", 8000, ["Gunjur", "Varthur", "Panathur"]),
    "Hebbal": ("North", "BBMP", 12000, ["Kempapura", "Amruthahalli"]),
    "Thanisandra": ("North", "BBMP", 9500, ["Thanisandra", "Byrathi"]),
    "Hennur": ("North", "BBMP", 8500, ["Hennur", "Kothanur", "Chellakere"]),
    "Yelahanka": ("North", "BBMP", 8000, ["Jakkur", "Allalasandra", "Singanayakanahalli"]),
    "Devanahalli": ("North", "BIAAPA", 6500, ["Bagalur", "Kannamangala", "Bettahalasur"]),
    "Electronic City": ("South", "BMRDA", 6500, ["Hebbagodi", "Neeladri", "Doddathogur"]),
    "Kanakapura Road": ("South", "BDA", 8000, ["Thalaghattapura", "Gubbalala", "Vajarahalli"]),
    "Bannerghatta Road": ("South", "BBMP", 9000, ["Hulimavu", "Gottigere", "Arekere"]),
    "Budigere Cross": ("East", "BMRDA", 6000, ["Budigere", "Mandur", "Kannamangala"]),
}
OUTSKIRTS = {"Devanahalli", "Electronic City", "Budigere Cross", "Kanakapura Road"}
SIZES = {2: 1150, 3: 1650, 4: 2600}  # typical carpet+common sqft per BHK

FIRST = [
    "Aditya", "Ananya", "Arvind", "Bhavana", "Chetan", "Deepa", "Ganesh", "Harsha",
    "Ishaan", "Jyothi", "Karthik", "Kavya", "Lokesh", "Madhuri", "Manjunath", "Nandini",
    "Naveen", "Pallavi", "Prakash", "Pooja", "Raghav", "Rekha", "Sandeep", "Shruti",
    "Srinivas", "Swathi", "Tejas", "Usha", "Vinay", "Vidya", "Yash", "Zoya",
    "Farhan", "Imran", "Joseph", "Mary", "Thomas", "Gurpreet", "Ritu", "Sameer",
]
LAST = [
    "Rao", "Reddy", "Hegde", "Iyer", "Menon", "Pai", "Gowda", "Shenoy", "Kamath",
    "Naidu", "Prabhu", "Bhat", "Kini", "Acharya", "Patil", "Desai", "Joshi",
    "Khan", "D'Souza", "Fernandes", "Singh", "Malhotra", "Krishnan", "Murthy",
]
_used_names: set[str] = set()


def person_name() -> str:
    while True:
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in _used_names:
            _used_names.add(n)
            return n


PROJECT_SUFFIX = [
    "Meadows", "Residency", "Skyline", "Greens", "Enclave", "Orchid", "Serenity", "Horizon",
    "Pinnacle", "Woods", "Springs", "Heights", "Gardens", "Vista", "Crest", "Lakeview",
    "Palms", "Nest", "Grandeur", "Avenue", "Boulevard", "Courtyard", "Terraces", "Haven",
]
CONTRACTORS = [
    "Deccan BuildCorp", "Vajra Engineering", "Kalinga Structures", "Sterling EPC",
    "Nirmaan Works", "Prithvi Constructions", "Apex Civil Works", "Udaya Engineers",
    "Granite Line Projects", "Titan Buildtech",
]
CLEAN_CONTRACTORS = {"Vajra Engineering", "Sterling EPC", "Udaya Engineers"}
LENDERS = [
    "Cauvery Housing Finance", "Malnad Capital", "Karnataka Urban Credit Co-op",
    "Silkboard Finserv", "Deccan Home Loans", "Nilgiri Capital Partners",
]
# (brand, since, quality)
GROUPS = [
    ("Aravalli Estates", 2001, "good"),
    ("Kaveri Crest Developers", 2006, "good"),
    ("Nandi Heights Group", 1998, "average"),
    ("Tungabhadra Homes", 2010, "average"),
    ("Greenfield Habitats", 2003, "excellent"),
    ("Mallige Constructions", 2008, "poor"),
    ("Chalukya Realty", 2005, "poor"),
    ("Banyan Grove Developers", 2004, "excellent"),
    ("Lakeshore Infra", 2011, "average"),
    ("Hoysala Living", 1996, "excellent"),
    ("Sahyadri Spaces", 2012, "average"),
    ("Coral Bay Developers", 2014, "average"),
    ("Meridian Urban", 2016, "good"),
    ("Vrindavan Realty", 2009, "good"),
]
DELAYS = {
    "excellent": [0, 0, 0, 0, 2, 3, 4],
    "good": [0, 0, 0, 0, 2, 4, 6, 9],
    "average": [0, 0, 2, 4, 6, 9, 14],
    "poor": [4, 8, 12, 16, 24],
}

# --------------------------------------------------------------------------- base entities
loc_ids = {}
for name, (zone, authority, rate, _villages) in LOCALITIES.items():
    loc_ids[name] = add_node("Locality", name=name, zone=zone, authority=authority, rate_per_sqft=rate)

contractor_ids = {c: add_node("Contractor", name=c, founded=rng.randint(1990, 2015)) for c in CONTRACTORS}
lender_ids = [add_node("Lender", name=l) for l in LENDERS]

landowner_ids = [add_node("Person", name=person_name(), role="landowner") for _ in range(40)]
aggregator_ids = [
    add_node("Company", name=n, status="active", incorporated=rng.randint(1995, 2015), business="land aggregation")
    for n in ["Bhoomi Land Aggregators Pvt Ltd", "Greenbelt Landholdings LLP", "Orchard Acres Pvt Ltd",
              "Ridge Valley Land Co", "Sunfield Estates Pvt Ltd"]
]

_project_names: set[str] = set()
_rera_seq = [1000]


def rera_id(year: int) -> str:
    _rera_seq[0] += rng.randint(3, 40)
    return f"PRM/KA/RERA/1251/{rng.randint(300, 470)}/PR/{year % 100:02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}/{_rera_seq[0]:06d}"


def price_range(locality: str, bhk: list[int]) -> tuple[int, int]:
    rate = LOCALITIES[locality][2] * rng.uniform(0.9, 1.15)
    lo = round(rate * SIZES[min(bhk)] / 1e5)
    hi = round(rate * SIZES[max(bhk)] / 1e5 * 1.08)
    return lo, hi


def plan_authority(locality: str, year: int) -> str:
    auth = LOCALITIES[locality][1]
    # BBMP's planning powers moved to the Greater Bengaluru Authority in 2025.
    return "GBA" if auth == "BBMP" and year >= 2025 else auth


def add_land(locality: str, khata: str | None = None, owner: str | None = None, until: int = 2015) -> str:
    village = rng.choice(LOCALITIES[locality][3])
    if khata is None:
        khata = "B" if locality in OUTSKIRTS and rng.random() < 0.18 else "A"
    survey = f"Sy. No. {rng.randint(10, 240)}/{rng.randint(1, 9)}"
    lid = add_node(
        "LandParcel", name=f"{survey}, {village}", survey_no=survey, village=village,
        acres=round(rng.uniform(1.5, 12), 1), khata=khata,
    )
    owner = owner or (rng.choice(landowner_ids) if rng.random() < 0.7 else rng.choice(aggregator_ids))
    add_edge("PREVIOUSLY_OWNED_BY", lid, owner, until=until)
    return lid


def add_complaints(project: str, short: str, n: int, open_share: float, year0: int, categories=None) -> None:
    categories = categories or ["delay", "delay", "quality", "refund", "amenities"]
    for _ in range(n):
        filed = rng.randint(min(year0, 2026), 2026)
        cid = f"CMP/{filed % 100:02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}/{rng.randint(10000, 99999):07d}"
        status = "open" if rng.random() < open_share else "resolved"
        c = add_node(
            "Complaint", name=f"K-RERA {cid}", complaint_no=cid, forum="K-RERA",
            category=rng.choice(categories), status=status, filed=filed, about=short,
        )
        add_edge("AGAINST", c, project)


def add_approvals(project: str, locality: str, launch: int, status: str, rera: bool = True,
                  plan_status: str = "granted") -> None:
    if rera and launch >= 2017:
        add_edge("HAS_APPROVAL", project, add_node(
            "Approval", name=f"RERA registration ({launch})", type="RERA", status="granted",
            year=launch, ref=rera_id(launch)))
    auth = plan_authority(locality, launch - 1)
    add_edge("HAS_APPROVAL", project, add_node(
        "Approval", name=f"{auth} building plan", type=f"{auth} plan", status=plan_status, year=launch - 1))
    if status in {"under_construction", "delivered", "stalled"}:
        add_edge("HAS_APPROVAL", project, add_node(
            "Approval", name="Commencement certificate", type="CC", status="granted", year=launch))
    if status == "delivered":
        add_edge("HAS_APPROVAL", project, add_node(
            "Approval", name="Occupancy certificate", type="OC", status="granted", year=launch + 4))


def add_project(company: str, brand: str, locality: str, *, name: str | None = None, launch: int,
                status: str, delay: int, bhk: list[int], contractor: str, units: int | None = None,
                price: tuple[int, int] | None = None, land: str | None = None, node_id: str | None = None,
                highlights: str | None = None, rera: bool = True, plan_status: str = "granted") -> str:
    if name is None:
        while True:
            name = f"{brand.split()[0]} {rng.choice(PROJECT_SUFFIX)}"
            if name not in _project_names:
                break
    _project_names.add(name)
    lo, hi = price or price_range(locality, bhk)
    props = dict(
        name=name, status=status, launch_year=launch, promised_possession=launch + 4,
        delay_months=delay, bhk=bhk, price_min_lakh=lo, price_max_lakh=hi,
        units=units or rng.choice([120, 180, 240, 320, 400, 560, 720]), locality=locality,
    )
    if highlights:
        props["highlights"] = highlights
    if rera and launch >= 2017:
        props["rera_id"] = rera_id(launch)
    pid = add_node("Project", node_id=node_id, **props)
    add_edge("DEVELOPS", company, pid)
    add_edge("LOCATED_IN", pid, loc_ids[locality])
    add_edge("BUILT_ON", pid, land or add_land(locality, until=launch - 1))
    add_edge("BUILDS", contractor_ids[contractor], pid)
    if rng.random() < 0.65:
        add_edge("FINANCES", rng.choice(lender_ids), pid)
    add_approvals(pid, locality, launch, status, rera=rera, plan_status=plan_status)
    return pid


# --------------------------------------------------------------------------- background developers
group_ids: dict[str, str] = {}
group_people: dict[str, list[str]] = {}
group_companies: dict[str, list[str]] = {}
LOC_NAMES = list(LOCALITIES)

for brand, since, quality in GROUPS:
    gid = add_node("Group", name=brand, brand_since=since, status="active", track_record=quality)
    group_ids[brand] = gid
    people = [add_node("Person", name=person_name(), role="director") for _ in range(rng.randint(3, 5))]
    group_people[brand] = people
    group_companies[brand] = []
    home = rng.sample(LOC_NAMES, 3)
    preferred = (
        rng.sample(sorted(CLEAN_CONTRACTORS), 2) if quality == "excellent"
        else rng.sample([c for c in CONTRACTORS if c != "Titan Buildtech"], 2)
    )
    for i in range(rng.randint(2, 3)):
        loc_for_name = home[i % len(home)]
        cname = rng.choice([
            f"{brand.split()[0]} {loc_for_name.split()[0]} Projects Pvt Ltd",
            f"{brand.split()[0]} Habitats {['I', 'II', 'III'][i]} LLP",
            f"{brand.split()[0]} Realcon Pvt Ltd" if i == 0 else f"{brand.split()[0]} Infra {i} Pvt Ltd",
        ])
        cid = add_node("Company", name=cname, status="active", incorporated=rng.randint(since, 2022))
        group_companies[brand].append(cid)
        add_edge("CONTROLS", gid, cid)
        for p in rng.sample(people, rng.randint(2, min(3, len(people)))):
            add_edge("DIRECTOR_OF", p, cid, since=rng.randint(since, 2022))
        for _ in range(rng.randint(1, 3)):
            locality = rng.choice(home)
            launch = rng.randint(2014, 2026)
            if launch <= 2020:
                status = "delivered"
            elif launch <= 2023:
                status = rng.choice(["delivered", "under_construction", "under_construction"])
            elif launch <= 2025:
                status = "under_construction"
            else:
                status = rng.choice(["launch", "under_construction"])
            delay = rng.choice(DELAYS[quality])
            if status == "under_construction":
                delay = delay // 2
            if status == "launch":
                delay = 0
            contractor = rng.choice(preferred)
            if delay >= 12 and contractor in CLEAN_CONTRACTORS:
                contractor = rng.choice([c for c in CONTRACTORS if c not in CLEAN_CONTRACTORS and c != "Titan Buildtech"])
            bhk = rng.choice([[2, 3], [2, 3], [3], [3, 4], [2], [1, 2, 3][1:]])
            pid = add_project(cid, brand, locality, launch=launch, status=status, delay=delay,
                              bhk=bhk, contractor=contractor)
            short = nodes[pid]["name"]
            if status != "launch":
                n_c = (rng.randint(3, 8) if delay >= 12 else rng.choice([0, 0, 1, 2]))
                add_complaints(pid, short, n_c, open_share=0.45 if delay >= 12 else 0.15, year0=launch + 2)

# A few directors sit on boards across groups (normal in the industry).
brands = list(group_ids)
for _ in range(3):
    a, b = rng.sample(brands, 2)
    add_edge("DIRECTOR_OF", rng.choice(group_people[a]), rng.choice(group_companies[b]), since=rng.randint(2015, 2023))

# Background litigation: mostly resolved, a couple pending.
for _ in range(6):
    brand = rng.choice(brands)
    target = rng.choice(group_companies[brand])
    year = rng.randint(2016, 2025)
    case_no = f"C.C. {rng.randint(1000, 9999)}/{year}"
    k = add_node("CourtCase", name=f"{case_no} (cheque bounce)", case_no=case_no, court="Magistrate Court, Bengaluru",
                 nature="cheque_bounce", status=rng.choice(["disposed", "disposed", "pending"]), filed=year)
    add_edge("INVOLVES", k, target)

# One background B-khata project and one rejected approval, for realism.
bkh_brand = "Tungabhadra Homes"
add_project(group_companies[bkh_brand][0], bkh_brand, "Budigere Cross", name="Tungabhadra Green Acres",
            launch=2025, status="under_construction", delay=0, bhk=[2, 3], contractor="Nirmaan Works",
            land=add_land("Budigere Cross", khata="B"))
add_project(group_companies["Lakeshore Infra"][0], "Lakeshore Infra", "Hennur", name="Lakeshore Riverside",
            launch=2026, status="launch", delay=0, bhk=[3], contractor="Apex Civil Works", plan_status="rejected")

# --------------------------------------------------------------------------- STORY 1: Phoenix developer
ns_group = add_node("Group", "GRP-NORTHSTAR", name="Northstar Homes", brand_since=2009, status="defunct", track_record="poor")
ns_hab = add_node("Company", "CMP-NORTHSTAR-HAB", name="Northstar Habitats Pvt Ltd", status="insolvency", incorporated=2009)
ns_lake = add_node("Company", "CMP-NORTHSTAR-LAKE", name="Northstar Lakeside Developers LLP", status="struck_off", incorporated=2014)
add_edge("CONTROLS", ns_group, ns_hab)
add_edge("CONTROLS", ns_group, ns_lake)
rohan = add_node("Person", "PER-ROHAN", name="Rohan Kulkarni", role="director")
meera = add_node("Person", "PER-MEERA", name="Meera Shetty", role="director")
arjun = add_node("Person", "PER-ARJUN", name="Arjun Varma", role="director")
nikhil = add_node("Person", "PER-NIKHIL", name="Nikhil Bhat", role="director")
_used_names.update({"Rohan Kulkarni", "Meera Shetty", "Arjun Varma", "Nikhil Bhat"})
add_edge("DIRECTOR_OF", rohan, ns_hab, since=2009, until=2024)
add_edge("DIRECTOR_OF", meera, ns_hab, since=2012, until=2024)
add_edge("DIRECTOR_OF", arjun, ns_hab, since=2009, until=2023)
add_edge("DIRECTOR_OF", rohan, ns_lake, since=2014, until=2022)
add_edge("DIRECTOR_OF", arjun, ns_lake, since=2014, until=2022)

ns1 = add_project(ns_hab, "Northstar Homes", "Hennur", name="Northstar Palm Grove", node_id="PRJ-NS-PALM",
                  launch=2018, status="stalled", delay=58, bhk=[2, 3], contractor="Granite Line Projects", units=480)
ns2 = add_project(ns_lake, "Northstar Homes", "Thanisandra", name="Northstar Lake Vista", node_id="PRJ-NS-LAKE",
                  launch=2019, status="stalled", delay=46, bhk=[3], contractor="Granite Line Projects", units=360)
ns3 = add_project(ns_hab, "Northstar Homes", "Yelahanka", name="Northstar Meadows", node_id="PRJ-NS-MEADOWS",
                  launch=2015, status="delivered", delay=30, bhk=[2, 3], contractor="Deccan BuildCorp", units=300)
add_complaints(ns1, "Northstar Palm Grove", 34, 0.88, 2021, ["delay", "delay", "refund", "refund", "quality"])
add_complaints(ns2, "Northstar Lake Vista", 26, 0.85, 2022, ["delay", "delay", "refund"])
add_complaints(ns3, "Northstar Meadows", 9, 0.45, 2019)
k = add_node("CourtCase", "CRT-NCLT-NORTHSTAR", name="CP (IB) 214/BB/2024 (insolvency)", case_no="CP (IB) 214/BB/2024",
             court="NCLT Bengaluru", nature="insolvency", status="pending", filed=2024,
             parties="Financial creditor vs Northstar Habitats Pvt Ltd")
add_edge("INVOLVES", k, ns_hab)

sky_group = add_node("Group", "GRP-SKYVISTA", name="Skyvista Builders", brand_since=2025, status="active", track_record="new")
altura_co = add_node("Company", "CMP-ALTURA", name="Altura Habitat Pvt Ltd", status="active", incorporated=2025)
add_edge("CONTROLS", sky_group, altura_co)
add_edge("DIRECTOR_OF", rohan, altura_co, since=2025)
add_edge("DIRECTOR_OF", meera, altura_co, since=2025)
add_edge("DIRECTOR_OF", nikhil, altura_co, since=2025)
altura_land = add_land("Sarjapur Road", khata="A", owner=landowner_ids[0], until=2025)
altura = add_project(altura_co, "Skyvista Builders", "Sarjapur Road", name="Skyvista Altura", node_id="PRJ-ALTURA",
                     launch=2026, status="launch", delay=0, bhk=[2, 3], contractor="Titan Buildtech", units=420,
                     price=(96, 165), land=altura_land,
                     highlights="4 towers, 45,000 sq ft clubhouse, 80% open space, launch offer: 10:90 payment plan")

# --------------------------------------------------------------------------- STORY 2: Disputed land
venkatesh = add_node("Person", "PER-VENKATESH", name="Venkatesh Gowda", role="landowner")
_used_names.add("Venkatesh Gowda")
serene_land = add_land("Devanahalli", khata="A", owner=venkatesh, until=2024)
nodes[serene_land].update(name="Sy. No. 112/3, Bagalur", survey_no="Sy. No. 112/3", village="Bagalur", acres=7.5)
serene = add_project(group_companies["Kaveri Crest Developers"][0], "Kaveri Crest Developers", "Devanahalli",
                     name="Kaveri Crest Serene", node_id="PRJ-SERENE", launch=2025, status="under_construction",
                     delay=0, bhk=[2, 3], contractor=rng.choice(["Deccan BuildCorp", "Prithvi Constructions"]),
                     units=520, price=(72, 112), land=serene_land)
title_case = add_node("CourtCase", "CRT-TITLE-112", name="O.S. 4521/2023 (title dispute)", case_no="O.S. 4521/2023",
                      court="City Civil Court, Bengaluru", nature="title_dispute", status="pending", filed=2023,
                      parties="Legal heirs of late Muniyappa vs Venkatesh Gowda and others")
add_edge("INVOLVES", title_case, venkatesh)
add_edge("INVOLVES", title_case, serene_land)

# --------------------------------------------------------------------------- STORY 3: Contractor contagion
monsoon = add_node("Contractor", "CON-MONSOON", name="Monsoon Infra Contractors", founded=2012)
contractor_ids["Monsoon Infra Contractors"] = monsoon
orchid = add_project(group_companies["Hoysala Living"][0], "Hoysala Living", "Whitefield",
                     name="Hoysala Orchid Springs", node_id="PRJ-ORCHID", launch=2024, status="under_construction",
                     delay=0, bhk=[3, 4], contractor="Monsoon Infra Contractors", units=260, price=(162, 290),
                     highlights="Premium low-density, 3 & 4 BHK, by a 30-year brand")
for brand, loc, nm, d, nc in [
    ("Mallige Constructions", "Varthur", "Mallige Sunrise Heights", 22, 6),
    ("Chalukya Realty", "Kanakapura Road", "Chalukya Pearl Residency", 26, 8),
    ("Lakeshore Infra", "Hennur", "Lakeshore Tranquil Bay", 18, 5),
]:
    p = add_project(group_companies[brand][-1], brand, loc, name=nm, launch=2021, status="under_construction",
                    delay=d, bhk=[2, 3], contractor="Monsoon Infra Contractors")
    add_complaints(p, nm, nc, 0.7, 2024, ["delay", "delay", "quality"])
k = add_node("CourtCase", "CRT-MONSOON", name="C.C. 3381/2025 (cheque bounce)", case_no="C.C. 3381/2025",
             court="Magistrate Court, Bengaluru", nature="cheque_bounce", status="pending", filed=2025,
             parties="Steel supplier vs Monsoon Infra Contractors")
add_edge("INVOLVES", k, monsoon)

# --------------------------------------------------------------------------- STORY 4: Key person
suresh = add_node("Person", "PER-SURESH", name="Suresh Narayan", role="director")
_used_names.add("Suresh Narayan")
mer_heb = add_node("Company", "CMP-MERIDIAN-HEBBAL", name="Meridian Hebbal Estates Pvt Ltd", status="active", incorporated=2025)
add_edge("CONTROLS", group_ids["Meridian Urban"], mer_heb)
add_edge("DIRECTOR_OF", suresh, mer_heb, since=2025)
add_edge("DIRECTOR_OF", rng.choice(group_people["Meridian Urban"]), mer_heb, since=2025)
zenith = add_node("Company", "CMP-ZENITH", name="Zenith Landmark Ventures Pvt Ltd", status="struck_off", incorporated=2013)
silver = add_node("Company", "CMP-SILVERLINE", name="Silverline Realcon Pvt Ltd", status="insolvency", incorporated=2015)
for c, since, until in [(zenith, 2013, 2021), (silver, 2015, 2023)]:
    add_edge("DIRECTOR_OF", suresh, c, since=since, until=until)
for brand in ["Coral Bay Developers", "Sahyadri Spaces", "Vrindavan Realty"]:
    add_edge("DIRECTOR_OF", suresh, group_companies[brand][0], since=rng.randint(2016, 2022))
silver_proj = add_project(silver, "Silverline", "Budigere Cross", name="Silverline Meadows", node_id="PRJ-SILVERLINE",
                          launch=2018, status="stalled", delay=52, bhk=[2], contractor="Kalinga Structures")
add_complaints(silver_proj, "Silverline Meadows", 14, 0.8, 2021)
tranquil = add_project(mer_heb, "Meridian Urban", "Hebbal", name="Meridian Tranquil", node_id="PRJ-TRANQUIL",
                       launch=2026, status="launch", delay=0, bhk=[3, 4], contractor="Udaya Engineers",
                       units=240, price=(198, 340), highlights="Lake-facing towers near Hebbal flyover")

# --------------------------------------------------------------------------- Clean comparators (Sarjapur Road)
for brand, nm, launch, status, delay, bhk, price, contractor in [
    ("Banyan Grove Developers", "Banyan Grove Whispering Woods", 2025, "under_construction", 0, [2, 3], (92, 158), "Sterling EPC"),
    ("Greenfield Habitats", "Greenfield Sarjapur Greens", 2023, "under_construction", 2, [2, 3], (88, 150), "Vajra Engineering"),
    ("Hoysala Living", "Hoysala Lakeside Residency", 2024, "under_construction", 0, [3], (128, 172), "Udaya Engineers"),
]:
    p = add_project(group_companies[brand][-1], brand, "Sarjapur Road", name=nm, launch=launch, status=status,
                    delay=delay, bhk=bhk, price=price, contractor=contractor)
    add_complaints(p, nm, 1, 0.0, 2025)

# --------------------------------------------------------------------------- write
OUT.write_text(json.dumps({"seed": SEED, "generated_at": SEED_TS, "nodes": list(nodes.values()), "edges": edges}, indent=1))
by_kind: dict[str, int] = {}
for n in nodes.values():
    by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
print(f"Wrote {OUT.name}: {len(nodes)} nodes, {len(edges)} edges")
print("  " + ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())))
