"""
Generate 10 synthetic SOW/RFP PDFs for IT and Data Science domains.
Run: python tests/generate_sows.py
"""
from __future__ import annotations
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

OUT_DIR = Path(__file__).parent / "sows"
OUT_DIR.mkdir(exist_ok=True)

styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=14, spaceAfter=6, textColor=colors.HexColor("#1a1d27"))
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceAfter=4, textColor=colors.HexColor("#2e3148"))
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=10, spaceAfter=3)
BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontSize=9, leading=13, spaceAfter=4)
BOLD = ParagraphStyle("BOLD", parent=BODY, fontName="Helvetica-Bold")


def p(text, style=BODY):
    return Paragraph(text, style)


def spacer(h=0.15):
    return Spacer(1, h * inch)


def section(title, content_paragraphs):
    items = [p(title, H2), spacer(0.05)]
    items.extend(content_paragraphs)
    items.append(spacer(0.1))
    return items


def work_item_table(items):
    header = ["ID", "Work Item", "Timeline", "Entry Criteria", "Definition of Done / Exit Criteria"]
    data = [header] + items
    t = Table(data, colWidths=[0.6*inch, 1.6*inch, 1.1*inch, 1.5*inch, 1.8*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2e3148")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f8")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#ccccdd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP", (0, 0), (-1, -1), "CJK"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build_pdf(filename: str, story: list):
    path = OUT_DIR / filename
    doc = SimpleDocTemplate(
        str(path), pagesize=letter,
        leftMargin=0.8*inch, rightMargin=0.8*inch,
        topMargin=0.8*inch, bottomMargin=0.8*inch,
    )
    doc.build(story)
    print(f"  Generated: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

def it_01_ecommerce():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("E-Commerce Platform Development — RetailMax Corp", H2),
        spacer(),
        p("<b>Client:</b> RetailMax Corp | <b>Vendor:</b> TechBuild Solutions | <b>Budget:</b> $850,000 | <b>Duration:</b> 12 months | <b>Start:</b> 2026-08-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("RetailMax Corp requires a cloud-native, multi-tenant e-commerce platform supporting 500,000 concurrent users, integrated with third-party logistics, payment gateways (Stripe, PayPal), and a personalisation recommendation engine. The platform must be PCI-DSS Level 1 compliant from day one."),
            p("<b>Goals:</b> (1) Launch MVP storefront within 4 months. (2) Achieve 99.95% uptime SLA. (3) Process 10,000 orders/hour at peak. (4) Full PCI-DSS Level 1 certification within 6 months."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–4):</b> Product catalog, user authentication, shopping cart, Stripe payment integration, order management, email notifications. MVP is considered complete when 100 end-to-end purchase transactions succeed in staging without errors and all P0 defects are resolved."),
            p("<b>Full Scope:</b> Recommendation engine, multi-vendor marketplace, returns management, analytics dashboard, loyalty programme, mobile PWA."),
            p("<b>Out of Scope:</b> Physical POS integration, cryptocurrency payments, custom ERP connectors not listed herein."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "User Authentication & IAM", "Sprint 1–2\n(Wk 1–4)", "Design approved; dev env ready", "OAuth2/OIDC login, MFA enabled, OWASP auth checklist passed, zero P0 security findings in SAST scan"],
            ["WI-002", "Product Catalog Service", "Sprint 2–3\n(Wk 3–6)", "WI-001 done; DB schema approved", "CRUD APIs functional, search latency <200ms p99, 10k products loaded, API contract tests pass"],
            ["WI-003", "Shopping Cart & Checkout", "Sprint 3–4\n(Wk 5–8)", "WI-002 done; Stripe sandbox ready", "Cart persists across sessions, checkout completes <3s, PCI-DSS SAQ-D signed off by QSA"],
            ["WI-004", "Order Management System", "Sprint 4–5\n(Wk 7–10)", "WI-003 done", "Order lifecycle (placed→shipped→delivered) automated, webhook events tested, SLA alert configured"],
            ["WI-005", "Recommendation Engine", "Sprint 6–8\n(Wk 11–16)", "WI-002 done; ML infra provisioned", "CTR uplift ≥5% vs baseline in A/B test, model serves <100ms p99, bias audit passed"],
            ["WI-006", "Multi-Vendor Marketplace", "Sprint 8–10\n(Wk 15–20)", "WI-004 done; vendor onboarding flow approved", "Vendor self-onboarding <30 min, commission calculation accurate to $0.01, revenue split automated"],
            ["WI-007", "Analytics Dashboard", "Sprint 10–11\n(Wk 19–22)", "WI-004 done; BI tool selected", "Real-time sales KPIs refresh <5s, 6-month historical data loaded, role-based access enforced"],
            ["WI-008", "Performance & Load Testing", "Sprint 11–12\n(Wk 21–24)", "All WIs done", "Platform sustains 10,000 orders/hour; p99 latency <500ms; zero data-loss incidents during chaos tests"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> Vendor shall pay a penalty of 1.5% of the milestone value per calendar week of delay, up to a maximum of 15% of total contract value. Delays beyond 10 weeks on any critical-path milestone grant Client the right to terminate the contract without further liability."),
            p("<b>4.2 Subcontracting:</b> Vendor may subcontract non-core activities (e.g., QA, DevOps) with prior written consent from Client. Core development and architecture roles may not be subcontracted. All subcontractors must sign the Client's NDA and security policy."),
            p("<b>4.3 Indemnity:</b> Vendor shall indemnify and hold harmless Client against any third-party claims arising from Vendor's breach of this SoW, including IP infringement, data breaches attributable to Vendor's code, and gross negligence. Client indemnifies Vendor against claims arising from Client-provided data."),
            p("<b>4.4 Intellectual Property:</b> All deliverables, source code, documentation, and models produced under this SoW are works-for-hire and vest exclusively in Client upon payment. Vendor retains no licence to reuse deliverables for competing projects."),
            p("<b>4.5 Insurance:</b> Vendor must maintain: Professional Indemnity ≥ $2M per occurrence; Cyber Liability ≥ $5M per occurrence; General Liability ≥ $1M. Certificates of insurance to be provided within 14 days of contract signing."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> Static Application Security Testing must be integrated into the CI/CD pipeline. All code commits to main branch require a passing SAST scan (Snyk or Semgrep). Zero critical/high vulnerabilities may be present at any release gate."),
            p("<b>5.2 VAPT:</b> A full penetration test (web application + API layer) must be conducted by a CREST-certified third party prior to production go-live and annually thereafter. Vendor must remediate all critical findings within 14 days and high findings within 30 days."),
            p("<b>5.3 PCI-DSS:</b> Platform must achieve and maintain PCI-DSS Level 1 compliance. Quarterly ASV scans required. Cardholder data environment must be segmented and documented. QSA engagement is Vendor's responsibility and cost."),
        ]),
        *section("6. Project Closure", [
            p("Project closure requires: (1) All WIs marked Done per Definition of Done. (2) Signed UAT acceptance from Client. (3) PCI-DSS Level 1 AOC delivered. (4) VAPT final report with all criticals/highs resolved. (5) Operations runbook and DR plan delivered. (6) Knowledge transfer sessions completed (minimum 3 sessions). (7) 30-day hypercare period with SLA ≥99.9% completed without P0 incidents."),
        ]),
    ]
    build_pdf("it_01_ecommerce_platform.pdf", story)


def it_02_erp():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Enterprise ERP System Implementation — ManufaCo Industries", H2),
        spacer(),
        p("<b>Client:</b> ManufaCo Industries | <b>Vendor:</b> EnterpriseEdge Consulting | <b>Budget:</b> $2,400,000 | <b>Duration:</b> 18 months | <b>Start:</b> 2026-09-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("ManufaCo Industries requires a full ERP implementation covering Finance, Supply Chain, Manufacturing Execution, and HR modules across 8 manufacturing plants in 3 countries. The solution must integrate with existing SCADA systems and support 2,000 concurrent users."),
            p("<b>Goals:</b> (1) Decommission 5 legacy systems within 18 months. (2) Achieve real-time inventory visibility across all plants. (3) Reduce month-end close from 12 days to 3 days. (4) SOC 2 Type II certification within 12 months of go-live."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–6):</b> Finance module (GL, AR, AP), single-plant inventory management, basic HR (payroll, leave). MVP complete when month-end close executes successfully for 2 consecutive months with zero reconciliation errors."),
            p("<b>Out of Scope:</b> Custom SCADA connectors beyond provided API specs; third-party forecasting tools; mobile offline capability."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Finance Module (GL/AR/AP)", "Month 1–4", "Chart of accounts approved; legacy data mapped", "Trial balance matches legacy to $0.01; parallel run for 1 month passed; CFO sign-off"],
            ["WI-002", "Inventory & Warehouse Mgmt", "Month 3–7", "WI-001 done; warehouse layout documented", "Real-time stock levels accurate ±0.5%; FIFO costing validated; 99.9% barcode scan accuracy"],
            ["WI-003", "Manufacturing Execution System", "Month 5–10", "WI-002 done; BOM data migrated", "Work orders auto-generated from demand plan; OEE dashboard live; SCADA integration tested"],
            ["WI-004", "HR & Payroll Module", "Month 6–10", "Org structure approved; payroll rules configured", "Payroll processed accurately for 3 consecutive months; compliance with 3 country labour laws verified"],
            ["WI-005", "Supply Chain & Procurement", "Month 8–13", "WI-002 done; vendor master clean", "PO-to-payment cycle <5 days; 3-way match automated; supplier portal live"],
            ["WI-006", "Reporting & BI Layer", "Month 12–15", "All modules live; data warehouse provisioned", "Executive dashboard refresh <10s; 24-month historical data loaded; role-based security enforced"],
            ["WI-007", "Multi-Site Rollout", "Month 13–18", "Single-site go-live stable 60 days", "All 8 plants live; local language/currency configured; global consolidation report accurate"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> Fixed penalty of $10,000 per calendar day for each missed milestone, with no grace period. Cumulative penalties capped at 20% of total contract value. Penalties deducted from final payment tranche."),
            p("<b>4.2 Subcontracting:</b> Subcontracting is strictly prohibited without prior written approval from Client's Board. Any unauthorised subcontracting constitutes a material breach and grounds for immediate termination. Approved subcontractors become jointly liable with Vendor."),
            p("<b>4.3 Indemnity:</b> Mutual indemnification: each party indemnifies the other against losses arising from its own breach, negligence, or wilful misconduct. Indemnity cap is 100% of total contract value for each party."),
            p("<b>4.4 Intellectual Property:</b> Joint ownership of all custom developments. Vendor retains ownership of its pre-existing IP and platform components; Client receives a perpetual, irrevocable, royalty-free licence to all Vendor IP embedded in deliverables. Custom integrations and configurations vest exclusively in Client."),
            p("<b>4.5 Insurance:</b> Vendor must maintain: Professional Indemnity ≥ $5M; Cyber Liability ≥ $10M; Workers Compensation as required by law. Vendor must name Client as additional insured on all policies."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All custom code must undergo SAST scan (Checkmarx) at pre-commit stage. No code may be promoted to staging with unresolved critical or high findings. Weekly SAST summary reports to Client's CISO."),
            p("<b>5.2 VAPT:</b> Quarterly penetration tests required during development phase; semi-annual post go-live. Scope includes ERP application, APIs, network layer, and SCADA integration points. All criticals resolved within 7 days."),
            p("<b>5.3 SOC 2 Type II:</b> Vendor is responsible for maintaining SOC 2 Type II readiness. Evidence collection tooling must be in place from Month 1. Initial Type I report required by Month 8."),
        ]),
        *section("6. Project Closure", [
            p("Closure requires: (1) All 7 WIs complete per DoD. (2) All 8 plants live and stable 30 days. (3) SOC 2 Type I report delivered. (4) All critical VAPT findings remediated. (5) Legacy system decommission plan executed. (6) 90-day hypercare with dedicated support team. (7) Client IT team trained and certified on platform administration."),
        ]),
    ]
    build_pdf("it_02_erp_implementation.pdf", story)


def it_03_cloud_migration():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Cloud Migration & Infrastructure Modernisation — FinServe Bank", H2),
        spacer(),
        p("<b>Client:</b> FinServe Bank | <b>Vendor:</b> CloudShift Partners | <b>Budget:</b> $1,200,000 | <b>Duration:</b> 9 months | <b>Start:</b> 2026-07-15"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("FinServe Bank requires migration of 42 on-premise applications to AWS (primary) and Azure (DR), achieving ISO 27001 certification and reducing infrastructure OPEX by 35%. All migrations must comply with banking regulatory requirements (RBI/SEBI guidelines for cloud adoption)."),
            p("<b>Goals:</b> (1) Zero-downtime migration for all Tier-1 banking applications. (2) ISO 27001 certification within 6 months post-migration. (3) 35% OPEX reduction verified by independent auditor. (4) RTO <4h, RPO <15min for all Tier-1 systems."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–3):</b> Migrate 5 non-critical internal applications to AWS; establish landing zone with IAM, VPC, CloudTrail, Config, Security Hub. MVP complete when all 5 apps pass 30-day stability test in cloud with no production incidents."),
            p("<b>Out of Scope:</b> Application re-architecture (lift-and-shift only unless specified); end-user device management; SaaS procurement."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Landing Zone & Security Baseline", "Month 1–2", "AWS/Azure accounts provisioned; network design approved", "All CIS Benchmark controls implemented; CloudTrail active; Security Hub score ≥80%; no public S3 buckets"],
            ["WI-002", "Non-Critical App Migration (Wave 1)", "Month 2–3", "WI-001 done; app inventory complete", "5 apps migrated; 30-day stability passed; rollback tested; cost baseline established"],
            ["WI-003", "Tier-2 App Migration (Wave 2)", "Month 3–5", "WI-002 stable; DR strategy approved", "15 apps migrated; zero-downtime cutovers; automated failover tested; BCDR documented"],
            ["WI-004", "Tier-1 Banking Apps (Wave 3)", "Month 5–7", "WI-003 stable; regulator NOC obtained", "All Tier-1 apps migrated; RTO <4h DR drill passed; regulator sign-off; zero data loss"],
            ["WI-005", "ISO 27001 Implementation", "Month 4–8", "Gap assessment complete; ISMS scope defined", "All 114 controls implemented; internal audit passed; Stage 2 audit initiated with accredited body"],
            ["WI-006", "Cost Optimisation & Rightsizing", "Month 7–9", "All apps stable in cloud", "35% OPEX reduction demonstrated; Reserved Instance plan in place; monthly FinOps report live"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> Any critical-path milestone delayed beyond 30 days grants Client the right to terminate the contract for cause with no further payment obligation. For delays under 30 days, a penalty of 0.75% of milestone value per week applies."),
            p("<b>4.2 Subcontracting:</b> Vendor may subcontract up to 30% of effort to pre-approved partners. Subcontractor list must be disclosed at contract signing. New subcontractors require 14-day written notice and Client approval. Vendor remains primary liable party."),
            p("<b>4.3 Indemnity:</b> Vendor indemnifies Client against any regulatory penalties arising from migration activities, data breaches during migration, and non-compliance with banking regulations. Mutual indemnity cap at 150% of total contract value."),
            p("<b>4.4 Intellectual Property:</b> Vendor grants Client a perpetual, non-exclusive licence to all migration scripts, IaC templates, and runbooks produced under this SoW. Vendor retains ownership but may not use Client-specific configurations for other banking clients."),
            p("<b>4.5 Insurance:</b> Cyber Liability ≥ $15M (mandatory given banking data); Professional Indemnity ≥ $5M; Directors & Officers liability ≥ $2M."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All IaC (Terraform/CloudFormation) must be scanned with Checkov or tfsec before deployment. No infrastructure may be provisioned with critical misconfigurations. Results integrated into CI/CD pipeline."),
            p("<b>5.2 VAPT:</b> Full VAPT required before each Wave migration and before ISO 27001 Stage 2 audit. Scope: network, API, web application, and cloud configuration review. Vendor must use an RBI-empanelled security firm."),
            p("<b>5.3 Continuous Compliance:</b> AWS Config Rules and Azure Policy must enforce compliance continuously. Any drift triggers automatic alert to Client's SOC within 15 minutes. Monthly compliance posture report to Client CISO."),
        ]),
        *section("6. Project Closure", [
            p("Closure requires: (1) All 42 applications migrated and stable 60 days. (2) ISO 27001 certificate issued. (3) 35% OPEX reduction confirmed by independent auditor. (4) All VAPT criticals/highs resolved. (5) Data centre decommission plan submitted. (6) Knowledge transfer to Client cloud team complete (minimum 40 hours of training)."),
        ]),
    ]
    build_pdf("it_03_cloud_migration.pdf", story)


def it_04_cybersecurity():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Enterprise Cybersecurity Infrastructure — GovProtect Agency", H2),
        spacer(),
        p("<b>Client:</b> GovProtect Agency | <b>Vendor:</b> ShieldForce Security | <b>Budget:</b> $3,100,000 | <b>Duration:</b> 15 months | <b>Start:</b> 2026-10-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("GovProtect Agency requires implementation of a zero-trust security architecture covering 12,000 endpoints, a Security Operations Centre (SOC), SIEM/SOAR platform, and privileged access management. All systems must comply with NIST CSF 2.0, FISMA High, and FedRAMP Moderate baselines."),
            p("<b>Goals:</b> (1) Achieve FISMA High authorisation within 12 months. (2) MTTD <1h for Tier-1 incidents. (3) MTTR <4h for critical incidents. (4) 100% endpoint coverage in EDR within 6 months. (5) Zero successful phishing credential harvests post-training."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–4):</b> SOC operational with 24/7 monitoring; EDR deployed to 80% of endpoints; SIEM collecting logs from all Tier-1 systems. MVP complete when SOC detects and responds to simulated APT exercise with MTTD <2h."),
            p("<b>Out of Scope:</b> Physical security systems; classified network (SIPR) integration; end-user application development."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Zero-Trust Architecture Design", "Month 1–2", "Current-state assessment complete; architecture board approval", "ZTA blueprint approved; identity provider integrated; micro-segmentation design signed off by CISO"],
            ["WI-002", "EDR Deployment (All Endpoints)", "Month 2–5", "WI-001 done; endpoint inventory complete", "100% Windows/Mac/Linux endpoints enrolled; policy tuned (FPR <1%); threat hunting playbooks delivered"],
            ["WI-003", "SIEM & Log Management", "Month 2–6", "Log source inventory complete; data classification done", "All Tier-1/2 log sources connected; retention 12 months hot/24 months cold; parser coverage >95%"],
            ["WI-004", "SOC Operations & Playbooks", "Month 3–7", "WI-003 live; analysts hired and trained", "50 detection rules active; 30 response playbooks documented; tabletop exercise passed"],
            ["WI-005", "PAM & Identity Governance", "Month 5–9", "WI-001 done; AD/LDAP mapped", "All privileged accounts in vault; session recording for admin access; JIT access for 100% of Tier-1 systems"],
            ["WI-006", "SOAR Automation", "Month 7–11", "WI-004 done; SOAR platform deployed", "MTTR reduced to <4h for Tier-1 playbooks; 80% of low-severity alerts auto-remediated"],
            ["WI-007", "FISMA Authorisation Package", "Month 10–14", "All controls implemented; POA&Ms resolved", "System Security Plan complete; security assessment complete; AO sign-off received; ATO issued"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> 2% of milestone value per week of delay. Maximum cumulative penalty: 20% of total contract value. Delays to FISMA milestones trigger mandatory escalation to Agency CIO within 48 hours."),
            p("<b>4.2 Subcontracting:</b> Subcontracting of any security-cleared role is strictly prohibited. Non-cleared roles (e.g., hardware provisioning) may be subcontracted with ISSO written approval. All personnel must undergo agency background checks."),
            p("<b>4.3 Indemnity:</b> Vendor indemnifies Client in full against any breach, loss, or regulatory finding arising from Vendor's implementation, including zero-day exploitation of Vendor-deployed tools within 90 days of deployment. Indemnity unlimited for gross negligence."),
            p("<b>4.4 Intellectual Property:</b> All playbooks, detection rules, SOAR workflows, and documentation are works-for-hire and vest in Agency. Vendor may not reuse Agency-specific threat intelligence in any external product."),
            p("<b>4.5 Insurance:</b> Cyber Liability ≥ $20M; Professional Indemnity ≥ $10M; Crime/Fidelity Bond ≥ $5M. All policies must have government contractor endorsements."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All custom SOAR playbooks and detection rule code undergo continuous SAST scanning. Vendor's internal SDLC must be NIST SP 800-218 (SSDF) compliant. Evidence of SAST pipeline shared monthly."),
            p("<b>5.2 VAPT:</b> Monthly automated vulnerability scanning; full red team exercise at Month 6 and Month 12 by an independent government-approved assessor. Red team scope: full kill chain including phishing, exploitation, and lateral movement."),
            p("<b>5.3 Penetration Testing:</b> Continuous attack surface management; quarterly web-app pen test; annual ICS/OT-scope assessment. All critical findings must be remediated within 72 hours with Client CISO sign-off."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) ATO issued by AO. (2) 100% endpoint EDR coverage maintained 90 days. (3) SOC MTTD <1h demonstrated in red team exercise. (4) All POA&M items closed. (5) Continuity of Operations (COOP) plan tested. (6) Full knowledge transfer to Agency SOC team. (7) 6-month retainer for incident response support included."),
        ]),
    ]
    build_pdf("it_04_cybersecurity_infrastructure.pdf", story)


def it_05_healthcare():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Healthcare Information System — MediCare Hospital Network", H2),
        spacer(),
        p("<b>Client:</b> MediCare Hospital Network | <b>Vendor:</b> HealthTech Innovations | <b>Budget:</b> $1,750,000 | <b>Duration:</b> 14 months | <b>Start:</b> 2026-08-15"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("MediCare requires a unified Electronic Health Record (EHR) and Patient Portal system covering 8 hospitals and 200 clinics, integrating with lab systems (HL7 FHIR R4), billing (EDI 837/835), and telehealth. Full HIPAA compliance is a hard requirement."),
            p("<b>Goals:</b> (1) Single patient record accessible across all sites within 6 months. (2) HIPAA BAA executed and controls implemented before any PHI is loaded. (3) Average EHR load time <2s. (4) Reduce medication errors by 40% via clinical decision support."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–5):</b> Core EHR (patient demographics, encounters, problem list, medications), HIPAA-compliant authentication, and integration with 2 lab systems. MVP complete when 500 real patient records (de-identified) load successfully and clinician acceptance test passes."),
            p("<b>Out of Scope:</b> Revenue cycle management beyond claim submission; medical device integration beyond specified lab interfaces; genomics data storage."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "HIPAA-Compliant Auth & PHI Controls", "Month 1–2", "HIPAA gap assessment complete; BAA signed", "Role-based access control (RBAC) for 15 clinical roles; audit logs for all PHI access; encryption at rest (AES-256) and in transit (TLS 1.3)"],
            ["WI-002", "Core EHR — Clinical Records", "Month 2–5", "WI-001 done; clinical workflow mapped", "Patient summary, encounter notes, problem list, medication list functional; HL7 FHIR R4 APIs passing conformance tests"],
            ["WI-003", "Lab & Imaging Integration", "Month 4–7", "WI-002 done; HL7 interfaces spec'd", "Results auto-populated in EHR <5 min of lab release; critical value alerts to clinician within 1 min; interface engine tested"],
            ["WI-004", "Patient Portal & Telehealth", "Month 5–9", "WI-002 done; patient consent workflows approved", "Patients access records within 24h of discharge (21st Century Cures Act); video consult <5s connection; WCAG 2.1 AA accessible"],
            ["WI-005", "Clinical Decision Support", "Month 7–10", "WI-002 done; clinical content licensed", "Drug-drug interaction alerts for 100% of orders; evidence-based order sets for 20 common diagnoses; 40% reduction in overridden alerts within 60 days"],
            ["WI-006", "Billing & Revenue Cycle", "Month 9–12", "WI-002 done; payer contracts loaded", "EDI 837P/837I claims submitted; ERA/835 auto-posted; denial rate <5%; first-pass resolution rate >85%"],
            ["WI-007", "Multi-Site Rollout & Data Migration", "Month 11–14", "WI-001-006 stable; legacy data mapped", "All 8 hospitals live; historical patient data migrated with 100% record integrity check; legacy system retired"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> $5,000 per calendar day for any missed milestone. Right to terminate for cause if any milestone is delayed more than 45 days. Termination triggers full refund of milestones not delivered."),
            p("<b>4.2 Subcontracting:</b> All subcontractors handling PHI must sign HIPAA-compliant Business Associate Agreements. Vendor requires written consent from Client before engaging any new subcontractor. Offshore subcontracting of PHI-touching work is prohibited."),
            p("<b>4.3 Indemnity:</b> Vendor solely responsible for HIPAA breach penalties arising from Vendor's system, code, or process failures. Client indemnifies Vendor against claims arising from clinician misuse of the system. No cap on indemnity for HIPAA violations."),
            p("<b>4.4 Intellectual Property:</b> Vendor retains ownership of base EHR platform; Client receives a perpetual, transferable licence. All custom modules (clinical decision support rules, MediCare-specific workflows) are works-for-hire and vest in Client. Source code escrow arrangement required."),
            p("<b>4.5 Insurance:</b> Professional Indemnity ≥ $5M; Cyber/HIPAA Liability ≥ $10M; Medical Malpractice (tech E&O) ≥ $3M. Policy must explicitly cover HIPAA breach notification costs."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST + DAST:</b> SAST (Veracode) required on all code commits; DAST (OWASP ZAP) required before each production release. No code may be released with CVSS ≥7.0 unresolved. DAST must cover all FHIR API endpoints."),
            p("<b>5.2 VAPT:</b> Full VAPT before each major release (Months 5, 9, 14) by a HIPAA-specialised firm. Scope: application, API, network, and social engineering (phishing simulation). VAPT report shared with Client's Privacy Officer."),
            p("<b>5.3 HIPAA Security Rule:</b> Complete HIPAA Security Rule risk analysis must be conducted and documented before any PHI is loaded. Technical safeguards (access control, audit controls, integrity, transmission security) must be implemented and tested. Annual risk assessment thereafter."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) All WIs complete per DoD. (2) HIPAA risk analysis complete with zero unresolved high risks. (3) All VAPT criticals resolved, third-party attestation provided. (4) Legacy systems decommissioned per plan. (5) Staff training completed (minimum 8 hours per clinical user). (6) 90-day hypercare with 24/7 PHI breach response SLA <1h. (7) Source code escrow funded and tested."),
        ]),
    ]
    build_pdf("it_05_healthcare_ehr.pdf", story)


def ds_01_churn():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Customer Churn Prediction Platform — TelecomPlus Pvt Ltd", H2),
        spacer(),
        p("<b>Client:</b> TelecomPlus Pvt Ltd | <b>Vendor:</b> DataMinds Analytics | <b>Budget:</b> $420,000 | <b>Duration:</b> 8 months | <b>Start:</b> 2026-09-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("TelecomPlus requires a real-time customer churn prediction platform processing 8 million subscriber records monthly, integrating with CRM and billing systems, and generating automated retention campaigns via a REST API. The model must be explainable (SHAP values) for regulatory purposes."),
            p("<b>Goals:</b> (1) Model AUC-ROC ≥ 0.87 on held-out test set. (2) Prediction latency <200ms per customer. (3) Reduce churn rate by 15% within 6 months of deployment. (4) Model bias audit passed (no demographic discrimination)."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–3):</b> Batch churn prediction pipeline, basic feature engineering (usage, billing, tenure), and CSV export for CRM upload. MVP complete when model AUC ≥ 0.82 on validation set and batch job runs without failure for 2 weeks."),
            p("<b>Out of Scope:</b> Real-time streaming beyond defined API; NLP on call recordings; multi-language support for explainability reports."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Data Pipeline & Feature Store", "Month 1–2", "Data access agreements signed; schemas documented", "ETL pipeline processes 8M records <4h; feature store versioned; data quality checks pass (null rate <0.1%)"],
            ["WI-002", "Churn Prediction Model (Batch)", "Month 2–4", "WI-001 done; feature set approved", "AUC-ROC ≥ 0.87; Precision ≥ 0.80 at 30% recall threshold; SHAP explanations generated for top-100 features"],
            ["WI-003", "Real-Time Prediction API", "Month 3–5", "WI-002 done; API contract approved", "REST API <200ms p99; autoscales to 500 RPS; versioned endpoints; API keys and rate limiting enforced"],
            ["WI-004", "CRM & Campaign Integration", "Month 5–7", "WI-003 done; CRM API credentials provided", "Predictions pushed to CRM daily; campaign trigger fires within 1h of churn flag; A/B test framework live"],
            ["WI-005", "Monitoring, Drift, Retraining", "Month 6–8", "WI-002 deployed; monitoring tooling selected", "PSI-based drift alerts; automated retraining when AUC drops >3%; model registry with approval workflow"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> 1% of milestone value per calendar week of delay. Maximum 12% of total contract value. Penalty waived if delay is caused by Client's failure to provide data access within agreed SLA."),
            p("<b>4.2 Subcontracting:</b> No subcontracting permitted. All model development, data engineering, and deployment must be performed by Vendor's permanent employees listed in Schedule A. Violation is material breach."),
            p("<b>4.3 Indemnity:</b> Client owns all subscriber data and indemnifies Vendor against claims arising from the data itself. Vendor indemnifies Client against algorithmic discrimination findings and model-related regulatory penalties."),
            p("<b>4.4 Intellectual Property:</b> All trained models, feature engineering pipelines, and associated code are works-for-hire. Client owns all deliverables. Vendor may not use TelecomPlus subscriber data or derived features for any other client. Vendor retains ownership of generic ML framework code."),
            p("<b>4.5 Insurance:</b> Professional Indemnity ≥ $2M; Cyber Liability ≥ $3M; covering AI/ML model liability is explicitly required."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All pipeline and API code scanned with Bandit (Python) and SonarQube. No high/critical findings at any release. Dependency scanning (SBOM) required for every release."),
            p("<b>5.2 Annual Security Audit:</b> Annual third-party security audit of the ML platform covering data access controls, model serving infrastructure, and API security. Audit report shared with Client within 30 days of completion."),
            p("<b>5.3 Data Privacy:</b> Subscriber PII must be pseudonymised before model training. All model inputs and outputs logged with 90-day retention. DPDP Act 2023 compliance required. Data processing agreement signed before any data is transferred."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) Model AUC ≥ 0.87 in production for 30 days. (2) Churn rate reduction ≥ 10% demonstrated (15% target over 6 months). (3) Bias audit passed. (4) SAST scan clean. (5) Monitoring dashboard operational. (6) Handover documentation complete including model card, data dictionary, and retraining runbook."),
        ]),
    ]
    build_pdf("ds_01_churn_prediction.pdf", story)


def ds_02_fraud():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Real-Time Fraud Detection System — PaySecure Financial", H2),
        spacer(),
        p("<b>Client:</b> PaySecure Financial | <b>Vendor:</b> FraudGuard AI | <b>Budget:</b> $680,000 | <b>Duration:</b> 10 months | <b>Start:</b> 2026-08-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("PaySecure requires a real-time fraud detection system processing 50,000 transactions per second with sub-50ms decision latency, integrating with payment rails (ISO 20022), and providing case management for fraud analysts. PCI-DSS Level 1 compliance is mandatory."),
            p("<b>Goals:</b> (1) Fraud detection rate ≥ 95% on card-not-present fraud. (2) False positive rate <0.5% to minimise customer friction. (3) Decision latency <50ms at p99. (4) Analyst case resolution time reduced by 30%."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–4):</b> Rule-based fraud engine for top-5 fraud patterns, transaction scoring API, and basic analyst dashboard. MVP complete when system processes 1,000 TPS with <50ms latency and detects all 5 synthetic fraud scenarios in UAT."),
            p("<b>Out of Scope:</b> AML (anti-money laundering) compliance beyond defined rules; voice/biometric authentication; cross-border FX fraud."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Real-Time Transaction Scoring Engine", "Month 1–3", "Data schema agreed; Kafka cluster provisioned", "Processes 50k TPS; p99 latency <50ms; circuit breaker active; zero data loss under load test"],
            ["WI-002", "ML Fraud Model (Gradient Boosting)", "Month 2–5", "WI-001 done; 24-month labelled data provided", "Fraud recall ≥ 95%; FPR <0.5% on holdout; model card with fairness metrics; SHAP explanations available"],
            ["WI-003", "Case Management & Analyst Tooling", "Month 4–7", "WI-001 done; analyst workflow documented", "Cases auto-created for flagged transactions; analyst SLA dashboard live; bulk resolution capability; audit trail complete"],
            ["WI-004", "ISO 20022 Integration", "Month 5–8", "WI-001 done; payment rail API keys provided", "All ISO 20022 message types parsed; enrichment from payment metadata; integration test with sandbox complete"],
            ["WI-005", "Model Monitoring & Champion-Challenger", "Month 7–10", "WI-002 deployed; monitoring agreed", "PSI alerts for feature drift; champion-challenger framework live; monthly model performance report to Client"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> $20,000 per calendar day after a 10-day grace period per milestone. No cap on cumulative penalties for Tier-1 milestones (WI-001, WI-002). Penalty for non-Tier-1 milestones capped at $100,000 each."),
            p("<b>4.2 Subcontracting:</b> Up to 20% of total effort may be subcontracted with 14-day written notice. Subcontractors must be disclosed by name, role, and access level. Any subcontractor with cardholder data access requires PCI-DSS compliant background check."),
            p("<b>4.3 Indemnity:</b> Mutual indemnity for respective breaches. Vendor indemnifies Client for fraud losses directly attributable to a system outage caused by Vendor's code during business hours (SLA: 99.99% uptime). Liability cap for such losses: $5M."),
            p("<b>4.4 Intellectual Property:</b> Mutual IP: Client owns all trained models and Client-specific feature engineering; Vendor retains fraud detection framework and generic ML components. Joint ownership of novel detection algorithms developed under this engagement; each party may license independently."),
            p("<b>4.5 Insurance:</b> Cyber Liability ≥ $20M (PCI mandate); Professional Indemnity ≥ $5M; Financial Institution Bond ≥ $10M; errors and omissions covering AI decision-making."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All code (Java/Python) scanned with Checkmarx at every PR merge. OWASP Top 10 compliance verified at each release gate. Dependency vulnerability scanning via OWASP Dependency-Check."),
            p("<b>5.2 VAPT:</b> Quarterly penetration tests during development; bi-annual post go-live. Scope: transaction API, case management portal, Kafka cluster, ML model endpoints. Scoping must include business logic testing for fraud bypass."),
            p("<b>5.3 PCI-DSS:</b> All cardholder data tokenised before storage. Encryption key management per PCI HSM standard. Network segmentation between fraud engine and cardholder data environment. ASV scans quarterly; QSA AOC required at go-live."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) Live fraud detection rate ≥ 93% for 30 days in production. (2) FPR <0.5% maintained. (3) PCI-DSS Level 1 AOC delivered. (4) All VAPT criticals resolved. (5) Analyst productivity improvement of ≥ 25% demonstrated. (6) Champion-challenger framework operational. (7) 60-day hypercare with fraud analyst support included."),
        ]),
    ]
    build_pdf("ds_02_fraud_detection.pdf", story)


def ds_03_supply_chain():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Supply Chain Analytics Platform — GlobalLogix Corp", H2),
        spacer(),
        p("<b>Client:</b> GlobalLogix Corp | <b>Vendor:</b> SupplyInsight Analytics | <b>Budget:</b> $550,000 | <b>Duration:</b> 9 months | <b>Start:</b> 2026-09-15"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("GlobalLogix requires a supply chain analytics platform covering demand forecasting, supplier risk scoring, and inventory optimisation across 15 countries and 3,000 SKUs, integrating with SAP ERP and 4 third-party logistics APIs."),
            p("<b>Goals:</b> (1) Forecast MAPE <8% at SKU/week level. (2) Inventory holding cost reduced by 20%. (3) Supplier risk score available for 100% of Tier-1 suppliers within 6 months. (4) Platform uptime ≥ 99.9%."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–3):</b> Demand forecasting for top-100 SKUs (80% of volume), SAP data ingestion pipeline, and executive dashboard. MVP complete when MAPE <10% on held-out data and dashboard loads in <5s."),
            p("<b>Out of Scope:</b> Physical IoT sensor integration; supplier onboarding portal; blockchain provenance tracking."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "SAP Integration & Data Lake", "Month 1–2", "SAP RFC credentials provided; data schemas agreed", "Historical 3-year transaction data loaded; daily incremental sync <30 min; data quality score >95%"],
            ["WI-002", "Demand Forecasting Models", "Month 2–5", "WI-001 done; forecast hierarchy approved", "MAPE <8% on all 3,000 SKUs; seasonal decomposition working; forecast horizon 13 weeks; confidence intervals provided"],
            ["WI-003", "Supplier Risk Scoring", "Month 4–7", "WI-001 done; risk dimensions agreed; data sources contracted", "Risk score for 100% Tier-1 suppliers; weekly refresh; risk factors explainable; alert triggers to procurement team"],
            ["WI-004", "Inventory Optimisation Engine", "Month 5–8", "WI-002 done; inventory policy agreed", "Safety stock recommendations per SKU; reorder point calculation; 20% holding cost reduction validated in pilot"],
            ["WI-005", "3PL API Integration", "Month 6–9", "WI-001 done; 3PL API keys received", "Real-time shipment tracking ingested; ETD accuracy >85%; exception alerts <10 min of carrier update"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> 0.5% of milestone value per week of delay, maximum 15% of total contract value. Penalty does not apply if delay is caused by Client's failure to provide SAP access or data within 5 business days of written request."),
            p("<b>4.2 Subcontracting:</b> Subcontracting permitted for data engineering and BI development roles. Vendor must maintain a subcontractor register and provide it to Client monthly. No subcontractor may access Client trade data (pricing, supplier contracts) without separate NDA."),
            p("<b>4.3 Indemnity:</b> Vendor indemnifies Client against losses arising from erroneous inventory recommendations that exceed $100,000 in holding cost impact, where the error is attributable to Vendor's model defect. Liability capped at $500,000 per incident."),
            p("<b>4.4 Intellectual Property:</b> Joint ownership of the supply chain analytics platform. Client owns all trained models, data, and configurations. Vendor owns the underlying ML framework and may licence it to non-competing industries. No party may sublicence the joint platform without mutual consent."),
            p("<b>4.5 Insurance:</b> Professional Indemnity ≥ $3M; Cyber Liability ≥ $5M; Business Interruption coverage covering supply chain disruption events."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All Python and Scala pipeline code scanned with Bandit and SpotBugs in CI. SAST reports attached to each sprint review. No release with unresolved high-severity findings."),
            p("<b>5.2 VAPT (Semi-Annual):</b> Semi-annual VAPT covering the analytics portal, SAP integration layer, and 3PL APIs. Vendor responsible for scheduling and cost. Client receives full VAPT report and remediation tracking."),
            p("<b>5.3 Data Classification:</b> All supplier pricing and contract data classified as Confidential. Access limited to named analysts. Row-level security enforced in all dashboards. Annual data access review required."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) MAPE <8% in production for 60 days. (2) 20% inventory holding cost reduction demonstrated. (3) 100% Tier-1 supplier risk scores live. (4) Semi-annual VAPT complete with criticals resolved. (5) User training complete (all 50 supply chain analysts). (6) Platform operated by Client team independently for 30 days post-hypercare."),
        ]),
    ]
    build_pdf("ds_03_supply_chain_analytics.pdf", story)


def ds_04_nlp():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("NLP Document Intelligence Platform — LegalEagle LLP", H2),
        spacer(),
        p("<b>Client:</b> LegalEagle LLP | <b>Vendor:</b> TextIQ Systems | <b>Budget:</b> $380,000 | <b>Duration:</b> 7 months | <b>Start:</b> 2026-10-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("LegalEagle LLP requires an NLP-powered document intelligence platform for automated contract review, clause extraction, risk flagging, and semantic search across 500,000 legal documents. The platform must handle privileged attorney-client communications with strict data isolation."),
            p("<b>Goals:</b> (1) Clause extraction F1 ≥ 0.91 on validation set. (2) Risk flag precision ≥ 0.88 to minimise false alarms. (3) Semantic search returns relevant results in top-3 for 90% of queries. (4) All models explainable to non-technical partners."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–3):</b> Contract ingestion pipeline, clause extraction for 10 core clause types (liability cap, termination, indemnity, etc.), and basic search interface. MVP complete when extraction F1 ≥ 0.85 on 100 sample contracts reviewed by partner attorneys."),
            p("<b>Out of Scope:</b> Court document filing automation; multi-language support beyond English; litigation prediction."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "Document Ingestion & OCR Pipeline", "Month 1–2", "Document samples received; storage architecture approved", "10k documents ingested <8h; OCR accuracy >98% on scanned PDFs; metadata extraction complete; audit trail active"],
            ["WI-002", "Clause Extraction Models", "Month 2–4", "WI-001 done; annotation guidelines approved; 5k annotated contracts delivered", "F1 ≥ 0.91 on 20 clause types; confidence scores output; uncertain predictions flagged for human review"],
            ["WI-003", "Risk Flagging Engine", "Month 3–5", "WI-002 done; risk taxonomy approved by partners", "Precision ≥ 0.88; risk explanations in plain English; risk dashboard with drill-down; false alarm rate <12%"],
            ["WI-004", "Semantic Search & Q&A", "Month 4–6", "WI-001 done; search taxonomy defined", "Top-3 relevance for 90% of test queries; cross-document synthesis for Q&A; response latency <3s"],
            ["WI-005", "Fine-Tuned LLM for Legal Drafting Assist", "Month 5–7", "WI-002 done; base model selected; compute provisioned", "Draft suggestions accepted by attorneys ≥ 70% of time; hallucination rate <2% on clause suggestions; full audit log of all AI suggestions"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> Fixed $15,000 per week of delay per milestone. Attorney-client privilege breach caused by Vendor's system failure results in immediate contract termination and indemnification of all Client losses without cap."),
            p("<b>4.2 Subcontracting:</b> No subcontracting for core ML development or any personnel with access to privileged documents. Data annotation may be subcontracted to pre-approved vendors operating in ISO 27001-certified facilities with legal privilege protocols."),
            p("<b>4.3 Indemnity:</b> Vendor indemnifies Client for any privilege waiver or confidentiality breach attributable to Vendor's platform or personnel. Vendor indemnifies Client for IP infringement claims arising from use of third-party training data. No liability cap for privilege-related breaches."),
            p("<b>4.4 Intellectual Property:</b> Client owns all fine-tuned models, extracted clause libraries, and risk taxonomies. Vendor retains base NLP framework; grants Client perpetual licence. Vendor may not use LegalEagle documents to train models for any other client. Client's legal strategies and deal terms are Confidential Information with perpetual protection."),
            p("<b>4.5 Insurance:</b> Professional Indemnity ≥ $10M (legal tech specific); Cyber Liability ≥ $5M; Legal Malpractice Technology Errors & Omissions ≥ $5M."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All releases undergo SAST (Semgrep + Snyk) and secret scanning (Gitleaks). No secrets in code repositories. All dependencies pinned and SBOM generated per release. Legal document processing code reviewed by security team."),
            p("<b>5.2 VAPT (Pre-Deployment):</b> Full VAPT before each major release (Month 3 MVP, Month 7 GA) by an independent firm with legal-tech experience. Scope: web application, API layer, document storage, and access controls. Privilege-bypass test cases mandatory."),
            p("<b>5.3 Data Isolation:</b> Each law firm client's documents stored in separate encrypted namespaces. Cross-client data access is a critical security defect requiring immediate incident response. Annual ISO 27001 audit of document handling processes."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) Clause extraction F1 ≥ 0.91 in production. (2) Risk flag precision ≥ 0.88 on 500 live contracts. (3) Both VAPT reports completed with criticals resolved. (4) SAST scan clean on final release. (5) All fine-tuned model weights delivered to Client. (6) Model cards and data lineage documentation complete. (7) 45-day hypercare with 24/7 privilege breach incident response."),
        ]),
    ]
    build_pdf("ds_04_nlp_document_intelligence.pdf", story)


def ds_05_predictive_maintenance():
    story = [
        p("REQUEST FOR PROPOSAL & STATEMENT OF WORK", H1),
        p("Predictive Maintenance IoT Platform — IndustrialOps Manufacturing", H2),
        spacer(),
        p("<b>Client:</b> IndustrialOps Manufacturing | <b>Vendor:</b> SensorAI Technologies | <b>Budget:</b> $720,000 | <b>Duration:</b> 11 months | <b>Start:</b> 2026-09-01"),
        spacer(),
        *section("1. Project Overview & Goals", [
            p("IndustrialOps requires an IoT-based predictive maintenance platform for 850 CNC machines across 4 factories, ingesting 500 sensor streams at 1Hz, predicting failure 72 hours in advance, and integrating with SAP PM for work order generation."),
            p("<b>Goals:</b> (1) Failure prediction recall ≥ 90% with 72h advance notice. (2) False alarm rate <3% to prevent unnecessary downtime. (3) Unplanned downtime reduced by 35%. (4) Platform processes all 500 streams with <5 min latency."),
        ]),
        *section("2. Scope of Work & MVP", [
            p("<b>MVP (Month 1–4):</b> IoT ingestion pipeline for 50 machines (pilot factory), anomaly detection baseline, and maintenance alert API. MVP complete when system predicts 3 of 5 injected synthetic failure scenarios with 24h advance notice."),
            p("<b>Out of Scope:</b> Robotic process automation; autonomous work order approval; AR maintenance guidance; SCADA write-back commands."),
        ]),
        *section("3. Work Breakdown & Timelines", []),
        work_item_table([
            ["WI-001", "IoT Ingestion & Edge Computing", "Month 1–3", "Network topology documented; edge hardware procured", "500 streams ingested at 1Hz; edge pre-processing reduces bandwidth 60%; data loss rate <0.01% over 30 days"],
            ["WI-002", "Anomaly Detection & Failure Prediction", "Month 2–5", "WI-001 done (pilot); 18-month sensor history provided; failure labels annotated", "Recall ≥ 90% at 72h; FAR <3%; prediction confidence intervals; root cause feature attribution via SHAP"],
            ["WI-003", "SAP PM Integration", "Month 4–7", "WI-002 done; SAP PM RFC access; work order templates approved", "Work orders auto-generated for high-confidence predictions; spare part recommendations included; EWO integration tested"],
            ["WI-004", "Maintenance Dashboard & Mobile App", "Month 5–8", "WI-002 done; UX mockups approved", "Machine health heatmap updates <5 min; mobile push alerts to technician within 2 min of trigger; offline mode for 4 hours"],
            ["WI-005", "Full-Factory Rollout (4 Plants)", "Month 7–10", "WI-001-004 stable in pilot; network upgraded", "All 850 machines monitored; factory-specific models tuned; 35% downtime reduction validated vs baseline"],
            ["WI-006", "ICS Security Hardening", "Month 9–11", "WI-001-005 stable; ICS risk assessment complete", "OT network segmented from IT; all ICS components patched; IEC 62443 controls implemented; annual ICS pen test passed"],
        ]),
        spacer(),
        *section("4. Legal & Contractual Obligations", [
            p("<b>4.1 Late Delivery Penalty:</b> $8,000 per calendar day for Tier-1 milestones (WI-001, WI-002, WI-005). $3,000 per day for other milestones. Vendor must provide 30-day advance notice of potential delays."),
            p("<b>4.2 Subcontracting:</b> Vendor may subcontract edge hardware installation and network configuration with 30-day written notice to Client. Core ML development and ICS security hardening may not be subcontracted. All subcontractors must comply with IEC 62443 requirements for industrial environments."),
            p("<b>4.3 Indemnity:</b> Vendor indemnifies Client for any production loss or equipment damage directly caused by erroneous maintenance alerts (false negatives resulting in missed failures). Indemnity cap: $2M per incident. Client indemnifies Vendor for losses arising from Client's failure to act on a correctly issued alert."),
            p("<b>4.4 Intellectual Property:</b> Vendor retains ownership of the predictive maintenance platform and base ML models. Client receives an exclusive licence for the manufacturing sector in the 4 countries of operation. Vendor may not licence the same trained models (calibrated on Client's data) to direct competitors. Client owns all sensor data and failure label annotations."),
            p("<b>4.5 Insurance:</b> Product Liability ≥ $10M (covering industrial automation); Professional Indemnity ≥ $5M; Cyber Liability ≥ $5M with OT/ICS coverage; Business Interruption covering factory downtime events."),
        ]),
        *section("5. Security Requirements", [
            p("<b>5.1 SAST:</b> All edge firmware and cloud platform code scanned with Coverity (C/C++ firmware) and Bandit (Python cloud). Firmware builds require SAST sign-off before flashing to edge devices. Supply chain attestation (SLSA Level 2) required for all dependencies."),
            p("<b>5.2 VAPT:</b> VAPT before pilot go-live and full-factory rollout. Scope: cloud platform, edge devices, OT network, SAP integration, and mobile app. ICS-specific test cases (OT protocol fuzzing, Man-in-the-Middle on MQTT) mandatory."),
            p("<b>5.3 ICS Security Assessment:</b> IEC 62443 Zone and Conduit model must be documented before WI-006. Annual ICS penetration test by an ICS-CERT qualified firm. Any OT-scope critical finding halts production deployment until resolved and verified."),
        ]),
        *section("6. Project Closure", [
            p("Closure: (1) Failure recall ≥ 90% sustained 60 days across all 4 plants. (2) 35% downtime reduction validated. (3) ICS security assessment complete; all criticals resolved. (4) VAPT reports clear. (5) SAP PM integration tested and accepted. (6) Maintenance team trained (40h per technician). (7) 90-day hypercare with ICS incident response SLA <2h."),
        ]),
    ]
    build_pdf("ds_05_predictive_maintenance.pdf", story)


if __name__ == "__main__":
    print("Generating synthetic SOW PDFs...")
    it_01_ecommerce()
    it_02_erp()
    it_03_cloud_migration()
    it_04_cybersecurity()
    it_05_healthcare()
    ds_01_churn()
    ds_02_fraud()
    ds_03_supply_chain()
    ds_04_nlp()
    ds_05_predictive_maintenance()
    print(f"\nDone. {len(list(OUT_DIR.glob('*.pdf')))} PDFs in {OUT_DIR}")
