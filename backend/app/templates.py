"""Industry templates: what a newly registered company starts with.

Each template gives a fictional team (emails on the reserved `.example` domain, so nothing is ever delivered),
synthetic BigQuery tables (120 days ending yesterday, with one planted incident so "why did X drop?" has an
answer), a starter handbook that Vertex AI Search indexes, and example prompts for the meeting room.
"""
from __future__ import annotations

import html
import random
import re
from datetime import date, timedelta

DAYS = 120
INCIDENT_DAYS_AGO = 10


def _f(name: str, kind: str, description: str) -> dict:
    return dict(name=name, type=kind, description=description)


INDUSTRIES: dict[str, dict] = {
    "fintech": dict(
        label="Fintech / payments",
        team=[("Ananya Rao", "Head of Engineering"), ("Marcus Lee", "Security Lead"), ("Fatima Khan", "Payments Operations Lead"),
              ("Diego Alvarez", "Platform Engineer"), ("Meera Pillai", "Product Manager")],
        incident="a card-network gateway timeout that lowered payment success and raised API latency",
        targets=[("Payment success rate", ">= 98.5% daily; below 97% is a Sev2 incident"),
                 ("Payments API p95 latency", "<= 350 ms; above 500 ms is a Sev2 incident"),
                 ("Support tickets", "investigate any day 50% above the 7-day average")],
        tables={
            "daily_metrics": dict(description="One row per day of company-wide payments metrics.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("active_users", "INT64", "Users with at least one payment that day"),
                _f("transactions", "INT64", "Payment attempts"), _f("payment_success_rate", "FLOAT64", "Successful / attempted payments, 0-1"),
                _f("api_p95_ms", "INT64", "Payments API 95th percentile latency in milliseconds"),
                _f("revenue_usd", "FLOAT64", "Fee revenue in USD"), _f("support_tickets", "INT64", "New support tickets")]),
            "region_daily": dict(description="Daily payments per region.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("region", "STRING", "India, Southeast Asia, Middle East or Europe"),
                _f("transactions", "INT64", "Payment attempts"), _f("volume_usd", "FLOAT64", "Processed volume in USD"),
                _f("failure_rate", "FLOAT64", "Failed / attempted payments, 0-1")]),
            "merchants": dict(description="Merchant accounts (one row per merchant).", fields=[
                _f("merchant_id", "STRING", "Merchant ID"), _f("name", "STRING", "Merchant name"), _f("category", "STRING", "Business category"),
                _f("country", "STRING", "Country"), _f("plan", "STRING", "Starter, Growth or Enterprise"),
                _f("monthly_volume_usd", "FLOAT64", "Average monthly processed volume in USD"),
                _f("onboarded_on", "DATE", "Onboarding date"), _f("status", "STRING", "active or churned")]),
        },
        examples=[("chart", "Check the numbers", "Hey Tensor, how did our payment success rate change over the last two weeks?"),
                  ("sparkle", "Find the cause", "Hey Tensor, which region had the highest failure rate during the dip?"),
                  ("file", "Ask the handbook", "Hey Tensor, what does our handbook say counts as a Sev2 incident?"),
                  ("jira", "Plan the next steps", "Hey Tensor, create Jira tickets to investigate the payment failures and assign them to Fatima.")],
    ),
    "ecommerce": dict(
        label="E-commerce / retail",
        team=[("Priyanka Shah", "Head of E-commerce"), ("Tom Becker", "Engineering Manager"), ("Lakshmi Menon", "Supply Chain Lead"),
              ("Arjun Das", "Growth Marketer"), ("Sofia Rossi", "Customer Experience Lead")],
        incident="a checkout bug in the mobile app that lowered conversion",
        targets=[("Conversion rate", ">= 2.8% daily; below 2.2% is a Sev2 incident"),
                 ("Returns", "investigate a return rate above 8% of orders"),
                 ("Stock", "reorder when stock_units falls below reorder_point")],
        tables={
            "daily_sales": dict(description="One row per day of storefront performance.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("site_visits", "INT64", "Sessions on web and app"), _f("orders", "INT64", "Orders placed"),
                _f("conversion_rate", "FLOAT64", "Orders / site_visits, 0-1"), _f("revenue_usd", "FLOAT64", "Gross revenue in USD"),
                _f("avg_order_value_usd", "FLOAT64", "Revenue / orders"), _f("returns", "INT64", "Returned orders")]),
            "category_sales": dict(description="Daily sales per product category.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("category", "STRING", "Electronics, Fashion, Home, Beauty or Grocery"),
                _f("orders", "INT64", "Orders"), _f("revenue_usd", "FLOAT64", "Revenue in USD")]),
            "inventory": dict(description="Current stock per SKU.", fields=[
                _f("sku", "STRING", "Stock keeping unit"), _f("product_name", "STRING", "Product"), _f("category", "STRING", "Category"),
                _f("warehouse", "STRING", "Warehouse"), _f("stock_units", "INT64", "Units in stock"),
                _f("reorder_point", "INT64", "Reorder when stock falls below this"), _f("unit_cost_usd", "FLOAT64", "Unit cost in USD")]),
        },
        examples=[("chart", "Check the numbers", "Hey Tensor, how did our conversion rate change over the last two weeks?"),
                  ("sparkle", "Find a risk", "Hey Tensor, which products are below their reorder point?"),
                  ("file", "Ask the handbook", "Hey Tensor, what's our policy on returns investigations?"),
                  ("mail", "Draft a follow-up", "Hey Tensor, email Lakshmi the list of products we need to reorder.")],
    ),
    "saas": dict(
        label="SaaS / software",
        team=[("Kavya Iyer", "CTO"), ("Ben Carter", "Engineering Manager"), ("Nadia Hassan", "Customer Success Lead"),
              ("Rohan Gupta", "Site Reliability Engineer"), ("Emily Zhang", "Product Manager")],
        incident="a failed database migration that raised the API error rate",
        targets=[("API error rate", "<= 0.5% daily; above 2% is a Sev2 incident"),
                 ("Churn", "review any account with health_score below 40"),
                 ("Trial conversion", "target 18% of trials converting to paid")],
        tables={
            "daily_metrics": dict(description="One row per day of product and revenue metrics.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("daily_active_users", "INT64", "Users active that day"),
                _f("signups", "INT64", "New sign-ups"), _f("trials_started", "INT64", "Trials started"),
                _f("paid_conversions", "INT64", "Trials converted to paid"), _f("mrr_usd", "FLOAT64", "Monthly recurring revenue in USD"),
                _f("churned_accounts", "INT64", "Accounts that cancelled"), _f("api_error_rate", "FLOAT64", "Failed / total API requests, 0-1"),
                _f("support_tickets", "INT64", "New support tickets")]),
            "feature_usage": dict(description="Daily active users per feature.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("feature", "STRING", "Dashboards, Reports, Integrations, Automations or Mobile"),
                _f("active_users", "INT64", "Users of the feature that day")]),
            "accounts": dict(description="Customer accounts (one row per account).", fields=[
                _f("account_id", "STRING", "Account ID"), _f("company_name", "STRING", "Customer"), _f("plan", "STRING", "Starter, Team or Enterprise"),
                _f("seats", "INT64", "Paid seats"), _f("mrr_usd", "FLOAT64", "Monthly recurring revenue in USD"), _f("region", "STRING", "Region"),
                _f("signup_date", "DATE", "Sign-up date"), _f("health_score", "INT64", "Customer health, 0-100")]),
        },
        examples=[("chart", "Check the numbers", "Hey Tensor, what was our MRR at the end of last month compared to the month before?"),
                  ("sparkle", "Find a risk", "Hey Tensor, which enterprise accounts have a health score below 40?"),
                  ("file", "Ask the handbook", "Hey Tensor, what's our target for trial conversion?"),
                  ("jira", "Plan the next steps", "Hey Tensor, create Jira tickets to follow up on the API error spike and assign them to Rohan.")],
    ),
    "logistics": dict(
        label="Logistics / supply chain",
        team=[("Vikram Singh", "Head of Operations"), ("Grace Okafor", "Fleet Manager"), ("Neha Kulkarni", "Hub Operations Lead"),
              ("Sam Patel", "Data Analyst"), ("Irene Costa", "Customer Operations Lead")],
        incident="heavy rain and a sorting-system outage at the Mumbai hub that delayed deliveries",
        targets=[("On-time delivery rate", ">= 94% daily; below 88% is a Sev2 incident"),
                 ("Damaged shipments", "investigate above 0.5% of shipments"),
                 ("Fleet servicing", "every vehicle serviced at least every 90 days")],
        tables={
            "daily_operations": dict(description="One row per day of network-wide operations.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("shipments", "INT64", "Shipments delivered"),
                _f("on_time_rate", "FLOAT64", "On-time deliveries / shipments, 0-1"), _f("avg_delivery_hours", "FLOAT64", "Average hours from pickup to delivery"),
                _f("damaged_shipments", "INT64", "Shipments reported damaged"), _f("fuel_cost_usd", "FLOAT64", "Fuel cost in USD"),
                _f("revenue_usd", "FLOAT64", "Revenue in USD")]),
            "hub_daily": dict(description="Daily shipments per hub.", fields=[
                _f("day", "DATE", "Calendar day (UTC)"), _f("hub", "STRING", "Mumbai, Delhi, Bengaluru, Chennai or Kolkata"),
                _f("shipments", "INT64", "Shipments handled"), _f("on_time_rate", "FLOAT64", "On-time deliveries / shipments, 0-1")]),
            "fleet": dict(description="Vehicles (one row per vehicle).", fields=[
                _f("vehicle_id", "STRING", "Vehicle ID"), _f("type", "STRING", "Van, Truck or Bike"), _f("hub", "STRING", "Home hub"),
                _f("capacity_kg", "INT64", "Capacity in kg"), _f("status", "STRING", "active, maintenance or retired"),
                _f("km_this_month", "INT64", "Kilometres driven this month"), _f("last_service", "DATE", "Last service date")]),
        },
        examples=[("chart", "Check the numbers", "Hey Tensor, how did our on-time delivery rate change over the last two weeks?"),
                  ("sparkle", "Find the cause", "Hey Tensor, which hub was worst during the delay?"),
                  ("file", "Ask the handbook", "Hey Tensor, how often must vehicles be serviced?"),
                  ("jira", "Plan the next steps", "Hey Tensor, create Jira tickets to service the overdue vehicles and assign them to Grace.")],
    ),
}

DEMO_EXAMPLES = [
    ("file", "Check a document", "Hey Tensor, does this spec conflict with our internal cloud security guidelines?"),
    ("jira", "Plan the next steps", "Hey Tensor, turn those conflicts into Jira tickets for the security sprint and assign them to David."),
    ("mail", "Draft a follow-up", "Hey Tensor, email David the links to those new tickets and a short summary."),
    ("sparkle", "Find an answer", "Hey Tensor, what were our daily active users last week?"),
]


def examples(industry: str) -> list[dict]:
    rows = INDUSTRIES[industry]["examples"] if industry in INDUSTRIES else DEMO_EXAMPLES
    return [dict(icon=i, label=l, text=t) for i, l, t in rows]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def team(industry: str, company_name: str) -> list[dict]:
    domain = (slug(company_name).replace("-", "") or "company") + ".example"
    return [dict(key=n.split()[0].lower(), name=n, role=r, email=f"{n.split()[0].lower()}@{domain}", source="template")
            for n, r in INDUSTRIES[industry]["team"]]


# ---- synthetic data -------------------------------------------------------------------------------

def _days(end: date) -> list[date]:
    return [end - timedelta(days=DAYS - 1 - i) for i in range(DAYS)]


def _shape(i: int, day: date, rng: random.Random, growth: float = 0.0025) -> float:
    """Growth trend x weekly seasonality x noise."""
    weekly = 0.9 if day.weekday() >= 5 else 1.0
    return (1 + growth) ** i * weekly * (1 + rng.gauss(0, 0.025))


def _incident(i: int) -> float:
    """1.0 on the incident day, 0.4 the day after, 0 otherwise."""
    return {DAYS - INCIDENT_DAYS_AGO: 1.0, DAYS - INCIDENT_DAYS_AGO + 1: 0.4}.get(i, 0.0)


def generate(industry: str, seed: str, end: date | None = None) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    end = end or date.today() - timedelta(days=1)
    days = _days(end)
    out: dict[str, list[dict]] = {}
    if industry == "fintech":
        regions = {"India": 0.46, "Southeast Asia": 0.24, "Middle East": 0.16, "Europe": 0.14}
        out["daily_metrics"], out["region_daily"] = [], []
        for i, d in enumerate(days):
            s, k = _shape(i, d, rng), _incident(i)
            tx = int(84_000 * s)
            rate = round(min(0.995, 0.987 + rng.gauss(0, 0.0015)) - 0.026 * k, 4)
            out["daily_metrics"].append(dict(day=d, active_users=int(52_000 * s), transactions=tx, payment_success_rate=rate,
                                             api_p95_ms=int(285 + rng.gauss(0, 18) + 330 * k), revenue_usd=round(tx * 0.42 * rate, 2),
                                             support_tickets=int(140 * s + 230 * k + rng.gauss(0, 8))))
            for region, share in regions.items():
                hit = k * (1.6 if region == "Southeast Asia" else 0.6)
                out["region_daily"].append(dict(day=d, region=region, transactions=int(tx * share), volume_usd=round(tx * share * 31.5, 2),
                                                failure_rate=round(max(0.004, 0.013 + rng.gauss(0, 0.0015) + 0.03 * hit), 4)))
        cats = ["Food delivery", "Travel", "Electronics", "Fashion", "Education", "Healthcare", "Gaming", "Groceries"]
        countries = ["India", "Singapore", "UAE", "Indonesia", "Germany", "Malaysia"]
        out["merchants"] = [dict(merchant_id=f"M{1000 + n}", name=f"{rng.choice(_ADJ)} {rng.choice(_NOUN)}", category=rng.choice(cats),
                                 country=rng.choice(countries), plan=rng.choices(["Starter", "Growth", "Enterprise"], [6, 3, 1])[0],
                                 monthly_volume_usd=round(rng.lognormvariate(10.5, 1.0), 2),
                                 onboarded_on=end - timedelta(days=rng.randint(10, 900)), status=rng.choices(["active", "churned"], [9, 1])[0])
                            for n in range(200)]
    elif industry == "ecommerce":
        cats = {"Electronics": 0.3, "Fashion": 0.27, "Home": 0.18, "Beauty": 0.13, "Grocery": 0.12}
        out["daily_sales"], out["category_sales"] = [], []
        for i, d in enumerate(days):
            s, k = _shape(i, d, rng), _incident(i)
            visits = int(410_000 * s * (1.12 if d.weekday() >= 5 else 1))
            conv = round(0.031 + rng.gauss(0, 0.0012) - 0.012 * k, 4)
            orders = int(visits * conv)
            aov = round(46 + rng.gauss(0, 1.5), 2)
            out["daily_sales"].append(dict(day=d, site_visits=visits, orders=orders, conversion_rate=conv, revenue_usd=round(orders * aov, 2),
                                           avg_order_value_usd=aov, returns=int(orders * (0.055 + rng.gauss(0, 0.004)))))
            for c, share in cats.items():
                out["category_sales"].append(dict(day=d, category=c, orders=int(orders * share), revenue_usd=round(orders * share * aov * (1.6 if c == "Electronics" else 0.85), 2)))
        warehouses = ["Mumbai", "Delhi", "Bengaluru"]
        out["inventory"] = []
        for n in range(200):
            c = rng.choice(list(cats))
            reorder = rng.randint(20, 120)
            out["inventory"].append(dict(sku=f"SKU-{20000 + n}", product_name=f"{rng.choice(_ADJ)} {rng.choice(_PRODUCT[c])}", category=c,
                                         warehouse=rng.choice(warehouses), stock_units=max(0, int(rng.gauss(reorder * 2.2, reorder))),
                                         reorder_point=reorder, unit_cost_usd=round(rng.uniform(3, 220), 2)))
    elif industry == "saas":
        feats = {"Dashboards": 0.72, "Reports": 0.48, "Integrations": 0.31, "Automations": 0.22, "Mobile": 0.18}
        out["daily_metrics"], out["feature_usage"] = [], []
        mrr = 412_000.0
        for i, d in enumerate(days):
            s, k = _shape(i, d, rng, growth=0.003), _incident(i)
            dau = int(18_500 * s * (0.55 if d.weekday() >= 5 else 1))
            trials = int(160 * s)
            paid = int(trials * (0.18 + rng.gauss(0, 0.02)))
            churned = max(0, int(rng.gauss(3, 1.4) + 4 * k))
            mrr += paid * 95 - churned * 310
            out["daily_metrics"].append(dict(day=d, daily_active_users=dau, signups=int(trials * 1.9), trials_started=trials, paid_conversions=paid,
                                             mrr_usd=round(mrr, 2), churned_accounts=churned,
                                             api_error_rate=round(max(0.0008, 0.0035 + rng.gauss(0, 0.0006) + 0.041 * k), 4),
                                             support_tickets=int(95 * s + 180 * k + rng.gauss(0, 6))))
            for f, share in feats.items():
                out["feature_usage"].append(dict(day=d, feature=f, active_users=int(dau * share * (1 + rng.gauss(0, 0.03)))))
        out["accounts"] = [dict(account_id=f"A{3000 + n}", company_name=f"{rng.choice(_ADJ)} {rng.choice(_NOUN)}",
                                plan=(plan := rng.choices(["Starter", "Team", "Enterprise"], [5, 4, 1])[0]),
                                seats=(seats := {"Starter": rng.randint(3, 10), "Team": rng.randint(10, 60), "Enterprise": rng.randint(60, 600)}[plan]),
                                mrr_usd=round(seats * {"Starter": 12, "Team": 24, "Enterprise": 38}[plan], 2),
                                region=rng.choice(["India", "North America", "Europe", "APAC"]), signup_date=end - timedelta(days=rng.randint(20, 1100)),
                                health_score=max(5, min(100, int(rng.gauss(71, 18))))) for n in range(200)]
    elif industry == "logistics":
        hubs = {"Mumbai": 0.28, "Delhi": 0.25, "Bengaluru": 0.2, "Chennai": 0.15, "Kolkata": 0.12}
        out["daily_operations"], out["hub_daily"] = [], []
        for i, d in enumerate(days):
            s, k = _shape(i, d, rng, growth=0.0015), _incident(i)
            ships = int(36_000 * s)
            on_time = round(min(0.99, 0.952 + rng.gauss(0, 0.006)) - 0.11 * k, 4)
            out["daily_operations"].append(dict(day=d, shipments=ships, on_time_rate=on_time, avg_delivery_hours=round(29 + rng.gauss(0, 1.2) + 14 * k, 1),
                                                damaged_shipments=int(ships * (0.0032 + rng.gauss(0, 0.0004))), fuel_cost_usd=round(ships * 0.71, 2),
                                                revenue_usd=round(ships * 6.4, 2)))
            for h, share in hubs.items():
                hit = k * (2.6 if h == "Mumbai" else 0.35)
                out["hub_daily"].append(dict(day=d, hub=h, shipments=int(ships * share), on_time_rate=round(max(0.5, min(0.995, 0.952 + rng.gauss(0, 0.008)) - 0.11 * hit), 4)))
        out["fleet"] = [dict(vehicle_id=f"V-{500 + n}", type=(t := rng.choices(["Van", "Truck", "Bike"], [5, 2, 3])[0]), hub=rng.choice(list(hubs)),
                             capacity_kg={"Van": 900, "Truck": 7000, "Bike": 40}[t], status=rng.choices(["active", "maintenance", "retired"], [17, 2, 1])[0],
                             km_this_month=rng.randint(300, 5200), last_service=end - timedelta(days=rng.randint(3, 140))) for n in range(160)]
    else:
        raise ValueError(f"Unknown industry {industry}")
    return {t: [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()} for row in rows] for t, rows in out.items()}


_ADJ = ["Blue", "Bright", "Swift", "Green", "Silver", "Urban", "Prime", "Nova", "Coral", "Summit", "Lotus", "Peak", "True", "North", "Bold"]
_NOUN = ["Harbor", "Labs", "Market", "Works", "Bazaar", "Systems", "Foods", "Travel", "Studio", "Health", "Logistics", "Kart", "Pay", "Retail"]
_PRODUCT = {"Electronics": ["Headphones", "Smartwatch", "Charger", "Speaker", "Keyboard"], "Fashion": ["Jacket", "Sneakers", "Kurta", "Tote Bag", "Jeans"],
            "Home": ["Lamp", "Cushion", "Cookware Set", "Rug", "Curtains"], "Beauty": ["Serum", "Sunscreen", "Lipstick", "Face Wash", "Perfume"],
            "Grocery": ["Coffee", "Green Tea", "Basmati Rice", "Olive Oil", "Granola"]}


# ---- starter handbook ------------------------------------------------------------------------------

def handbook_html(industry: str, company_name: str, jira_key: str) -> str:
    """A short company handbook so search has something to cite from day one."""
    t = INDUSTRIES[industry]
    e = html.escape
    people = "".join(f"<tr><td>{e(p['name'])}</td><td>{e(p['role'])}</td><td>{e(p['email'])}</td></tr>" for p in team(industry, company_name))
    targets = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td></tr>" for a, b in t["targets"])
    tables = "".join(f"<li><b>{e(name)}</b>: {e(spec['description'])} Columns: "
                     + ", ".join(f"{e(f['name'])} ({e(f['description'])})" for f in spec["fields"]) + "</li>"
                     for name, spec in t["tables"].items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(company_name)} Company Handbook (HB-001)</title></head><body>
<h1>{e(company_name)} Company Handbook (HB-001)</h1>
<p>Industry: {e(t['label'])}. This handbook is the source of truth for service targets, incident severity, security and
data handling at {e(company_name)}. Jira project key: {e(jira_key)}.</p>
<h2>1. Team directory</h2><table><tr><th>Name</th><th>Role</th><th>Email</th></tr>{people}</table>
<h2>2. Service targets and incident severity</h2><table><tr><th>Measure</th><th>Target / threshold</th></tr>{targets}</table>
<p>Sev1: complete outage or data breach; page the on-call lead within 15 minutes and update stakeholders every 30 minutes.
Sev2: a threshold above is breached; acknowledge within 30 minutes and open a Jira ticket in {e(jira_key)} the same day.
Every Sev1 and Sev2 needs a written post-incident review within 5 working days.</p>
<h2>3. Security</h2><ul><li>All traffic uses TLS 1.2 or higher; TLS 1.0 and 1.1 are prohibited.</li>
<li>Customer data is encrypted at rest with customer-managed keys.</li><li>Access tokens expire after 60 minutes.</li>
<li>Production access requires single sign-on with multi-factor authentication.</li></ul>
<h2>4. Data retention</h2><ul><li>Audit logs: 365 days. Application logs: 90 days.</li>
<li>Customer personal data is deleted within 30 days of an account closing.</li></ul>
<h2>5. Data available to Tensor</h2><p>These BigQuery tables are refreshed daily:</p><ul>{tables}</ul>
<h2>6. Recent incidents</h2><p>{INCIDENT_DAYS_AGO} days ago: {e(t['incident'])}. It was resolved within a day; follow-up actions are tracked in {e(jira_key)}.</p>
</body></html>"""


def jira_key(company_name: str) -> str:
    letters = re.sub(r"[^A-Z]", "", company_name.upper())
    return (letters[:4] or "TEAM") if len(letters) >= 2 else "TEAM"

