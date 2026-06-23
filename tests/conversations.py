"""
Per-document test conversation scripts.

Each entry in CONVERSATIONS maps to a PDF filename and contains:
  - aligned:     messages that should score HIGH (>=60) with no/few deviations
  - misaligned:  messages that should score LOW (<=40) and/or trigger deviations
  - edge:        boundary cases — interesting but not clearly in or out
"""
from __future__ import annotations

CONVERSATIONS: dict[str, dict] = {
    "it_01_ecommerce_platform.pdf": {
        "aligned": [
            {
                "content": "We've completed WI-001 User Authentication with OAuth2 and MFA. SAST scan passed with zero critical findings. Ready to start WI-002 Product Catalog next sprint.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
            {
                "content": "The CREST-certified firm has completed the pre-launch penetration test. All critical vulnerabilities have been remediated within the 14-day window. PCI-DSS QSA engagement is scheduled for next week.",
                "tags": ["#architecture"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "We are proposing to deliver the MVP storefront by Month 4, which includes product catalog, shopping cart, and Stripe payment integration. 100 end-to-end purchase transactions have been validated in staging.",
                "tags": ["#feature", "#decision"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "The client has asked us to integrate a cryptocurrency payment gateway (Bitcoin and Ethereum) into the checkout flow. We think this is a good expansion of the platform.",
                "tags": ["#feature"],
                "expect_score_max": 40,
                "expect_out_of_scope": True,
                "expect_deviations": True,
            },
            {
                "content": "To save costs, we are proposing to skip the VAPT this year and instead rely on our internal security review. We'll do the PCI-DSS QSA next quarter.",
                "tags": ["#decision"],
                "expect_score_max": 30,
                "expect_deviations": True,
            },
            {
                "content": "We plan to offshore the entire backend development to a subcontracted team in another country without informing the client. This will help us cut costs by 40%.",
                "tags": ["#decision"],
                "expect_score_max": 25,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "The recommendation engine sprint is running 2 weeks behind schedule. We estimate the delay will not exceed 3 weeks total.",
                "tags": ["#concern"],
                "note": "Delay is within penalty threshold but should flag concern",
            },
            {
                "content": "We are exploring adding a physical POS integration as a future phase, not in the current sprint.",
                "tags": ["#feature"],
                "note": "POS is explicitly out of scope — should flag but low severity since marked future",
            },
        ],
    },

    "it_02_erp_implementation.pdf": {
        "aligned": [
            {
                "content": "Finance module parallel run completed successfully for 2 consecutive months. Trial balance matches legacy system to $0.01. CFO has signed off on WI-001. Starting inventory and warehouse module next.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "SOC 2 Type I evidence collection tooling is live from Month 1 as required. Internal audit is scheduled for Month 7, ahead of the Month 8 deadline for Type I report delivery.",
                "tags": ["#architecture"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
            {
                "content": "Quarterly VAPT has been completed. All critical findings resolved within 7 days. Security report delivered to Client CISO.",
                "tags": ["#decision"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to bring in a third-party ERP consulting firm to handle the manufacturing execution module without telling the client. They have better expertise in this area.",
                "tags": ["#decision"],
                "expect_score_max": 20,
                "expect_deviations": True,
            },
            {
                "content": "The client wants custom AI-powered forecasting tools that predict demand across all 8 plants. We think we should build this as an add-on to the ERP.",
                "tags": ["#feature"],
                "expect_score_max": 35,
                "expect_out_of_scope": True,
            },
            {
                "content": "We are planning to retain a copy of the custom ERP integrations we build for ManufaCo to reuse in our next ERP project. The client won't notice.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "The multi-site rollout for Plant 7 and Plant 8 will be delayed by 3 weeks due to network infrastructure issues on the client side.",
                "tags": ["#concern", "#blocker"],
                "note": "Delay caused by client — penalty may not apply, but should flag timeline risk",
            },
        ],
    },

    "it_03_cloud_migration.pdf": {
        "aligned": [
            {
                "content": "Wave 1 non-critical app migration completed. All 5 apps passed 30-day stability test. AWS Cost baseline established. Security Hub score is 87%, above the 80% threshold.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "IaC Terraform scan with tfsec is integrated into CI/CD. No critical misconfigurations in the last 3 deployments. Checkov also passing on all CloudFormation templates.",
                "tags": ["#architecture"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
            {
                "content": "ISO 27001 gap assessment completed. ISMS scope defined covering all 42 migrated applications. All 114 controls mapped. Internal audit scheduled for Month 7.",
                "tags": ["#decision"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to re-architect the core banking application from monolith to microservices during migration. This will make the cloud deployment cleaner.",
                "tags": ["#feature"],
                "expect_score_max": 30,
                "expect_out_of_scope": True,
                "expect_deviations": True,
            },
            {
                "content": "We brought in a new offshore subcontractor to handle the Tier-1 banking app migration without notifying the client. They are starting next week.",
                "tags": ["#decision"],
                "expect_score_max": 20,
                "expect_deviations": True,
            },
            {
                "content": "The OPEX reduction is only 18% so far, but we plan to tell the client it has reached 35% to close the project on time.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "The Tier-1 banking app migration is at risk due to regulator NOC taking longer than expected — estimated 6-week delay to WI-004.",
                "tags": ["#concern", "#blocker"],
                "note": "Regulator NOC is a legitimate external dependency — termination right may apply",
            },
        ],
    },

    "it_04_cybersecurity_infrastructure.pdf": {
        "aligned": [
            {
                "content": "EDR is deployed to 100% of Windows and Mac endpoints. Policy tuning complete with false positive rate below 1%. Threat hunting playbooks delivered to the Agency SOC team.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Monthly red team exercise completed. MTTD was 47 minutes, beating the 1-hour target. MTTR for the simulated Tier-1 incident was 3.5 hours. SOC team responding well.",
                "tags": ["#architecture"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "The System Security Plan is complete. All 800-53 controls are documented. Security assessment by the independent assessor is underway. ATO package ready for AO review.",
                "tags": ["#decision"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to subcontract the SOC analyst roles to a managed security service provider. This will give us 24/7 coverage without hiring additional staff.",
                "tags": ["#decision"],
                "expect_score_max": 20,
                "expect_deviations": True,
            },
            {
                "content": "We are planning to skip the monthly vulnerability scanning this quarter to focus on the FISMA package delivery. We'll catch up next quarter.",
                "tags": ["#decision"],
                "expect_score_max": 25,
                "expect_deviations": True,
            },
            {
                "content": "We want to reuse the agency-specific threat intelligence and detection rules we're building for GovProtect in our commercial SOC offering for private clients.",
                "tags": ["#decision"],
                "expect_score_max": 15,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "A zero-day exploit in our SOAR platform was disclosed 60 days after go-live. We have a patch ready but need 48 hours of maintenance window.",
                "tags": ["#concern", "#blocker"],
                "note": "Zero-day beyond 90-day indemnity window — liability question is ambiguous",
            },
        ],
    },

    "it_05_healthcare_ehr.pdf": {
        "aligned": [
            {
                "content": "HIPAA risk analysis is complete with zero unresolved high risks. BAA signed with all three subcontractors. Encryption at rest (AES-256) and in transit (TLS 1.3) verified. PHI controls passed internal audit.",
                "tags": ["#decision", "#architecture"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "FHIR R4 conformance tests passed for all 15 resource types. Lab results auto-populate in EHR within 3 minutes of lab release. Critical value alerts confirmed working in UAT.",
                "tags": ["#sprint"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "Patient portal has been tested for WCAG 2.1 AA accessibility. Patients can access discharge records within 24 hours, meeting the 21st Century Cures Act requirement.",
                "tags": ["#feature", "#decision"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to use MediCare's de-identified patient data to pre-train our general-purpose clinical NLP model that we will sell to other hospital networks.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
            {
                "content": "To meet the deadline, we plan to load PHI into the system before completing the HIPAA risk analysis and before the BAA is signed with subcontractors.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
            {
                "content": "We are planning to offshore the EHR customisation work (which involves viewing PHI) to a team in a country with no HIPAA equivalency without written consent.",
                "tags": ["#decision"],
                "expect_score_max": 15,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "The genomics data management feature has been requested by two large hospitals. Should we scope it in as a future phase paid addition?",
                "tags": ["#feature", "#concern"],
                "note": "Genomics storage is explicitly out of scope — should be flagged even as future phase",
            },
        ],
    },

    "ds_01_churn_prediction.pdf": {
        "aligned": [
            {
                "content": "Churn model AUC-ROC reached 0.89 on the held-out test set. SHAP values are available for the top 100 features. Bias audit passed — no demographic discrimination detected.",
                "tags": ["#decision", "#feature"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Prediction API is live with p99 latency of 142ms, well under the 200ms SLA. Autoscaling tested to 500 RPS. API keys and rate limiting enforced.",
                "tags": ["#sprint"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "SBOM generated for the current release. Bandit and SonarQube scans show zero high-severity findings. Dependency vulnerability report shared with client.",
                "tags": ["#architecture"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We plan to use TelecomPlus subscriber usage patterns and churn labels to improve our general-purpose telecom churn model that we sell as a SaaS product to other carriers.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
            {
                "content": "We are bringing in a freelance data scientist to help with model training. They will have full access to the subscriber dataset.",
                "tags": ["#decision"],
                "expect_score_max": 20,
                "expect_deviations": True,
            },
            {
                "content": "We want to add NLP sentiment analysis on customer call recordings to improve the churn model. This would significantly increase prediction accuracy.",
                "tags": ["#feature"],
                "expect_score_max": 35,
                "expect_out_of_scope": True,
            },
        ],
        "edge": [
            {
                "content": "The batch pipeline delay is caused by TelecomPlus IT team not providing DB access within the 5-business-day SLA. We are now 2 weeks behind on WI-001.",
                "tags": ["#concern", "#blocker"],
                "note": "Delay penalty waiver condition — client-caused delay",
            },
        ],
    },

    "ds_02_fraud_detection.pdf": {
        "aligned": [
            {
                "content": "Transaction scoring engine processing 52,000 TPS in load test with p99 latency of 38ms. Circuit breaker tested and active. Zero data loss confirmed over 72-hour soak test.",
                "tags": ["#sprint", "#decision"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Fraud recall is 96% on holdout set with FPR of 0.43%. SHAP explanations generated. Model card with fairness metrics submitted to client for review. Ready for production deployment.",
                "tags": ["#decision"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "PCI-DSS quarterly ASV scan complete. All cardholder data tokenised before storage. HSM-based key management verified by QSA. Network segmentation between fraud engine and CHD environment confirmed.",
                "tags": ["#architecture"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to expand scope to include AML transaction monitoring and SWIFT messaging compliance as part of the same platform delivery.",
                "tags": ["#feature"],
                "expect_score_max": 30,
                "expect_out_of_scope": True,
            },
            {
                "content": "System was down for 6 hours during business hours due to a bug in our deployment. We will not be reporting this to the client as it was during low-traffic hours.",
                "tags": ["#decision"],
                "expect_score_max": 15,
                "expect_deviations": True,
            },
            {
                "content": "We plan to use PaySecure's fraud pattern data (labelled transactions) to improve our generic fraud detection SaaS product.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "Biometric authentication (fingerprint + face) for high-value transactions has been requested by the client. Should we assess the feasibility?",
                "tags": ["#feature", "#concern"],
                "note": "Biometric auth is out of scope — should flag but feasibility discussion is reasonable",
            },
        ],
    },

    "ds_03_supply_chain_analytics.pdf": {
        "aligned": [
            {
                "content": "Demand forecasting model achieving MAPE of 6.8% on all 3,000 SKUs. 13-week forecast horizon confirmed. Confidence intervals included. Seasonal decomposition validated by supply chain team.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Risk scores published for 100% of Tier-1 suppliers. Weekly refresh automated. Risk factors explained to procurement team. High-risk supplier alerts delivered within 30 minutes.",
                "tags": ["#decision"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "Semi-annual VAPT completed by independent firm. All critical findings resolved. Analytics portal, SAP integration, and 3PL APIs all tested. Full report delivered to Client.",
                "tags": ["#architecture"],
                "expect_score_min": 60,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to integrate physical IoT sensors from warehouse floors to improve inventory accuracy in real time. This will significantly improve our forecasts.",
                "tags": ["#feature"],
                "expect_score_max": 30,
                "expect_out_of_scope": True,
            },
            {
                "content": "We are planning to share GlobalLogix's supplier pricing data and contract terms with our research team to publish an industry benchmark report.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
            {
                "content": "To accelerate delivery, we want to use a blockchain provenance tracking module from a partner company without informing GlobalLogix.",
                "tags": ["#decision"],
                "expect_score_max": 25,
                "expect_deviations": True,
                "expect_out_of_scope": True,
            },
        ],
        "edge": [
            {
                "content": "SAP access was only granted 8 business days after our written request, exceeding the 5-day SLA. We lost 3 days on WI-001. Is the late delivery penalty applicable?",
                "tags": ["#concern"],
                "note": "Penalty waiver condition triggered by client delay — ambiguous penalty applicability",
            },
        ],
    },

    "ds_04_nlp_document_intelligence.pdf": {
        "aligned": [
            {
                "content": "Clause extraction F1 reached 0.93 on 20 clause types including liability cap, termination, and indemnity. Confidence scores output for all predictions. Uncertain predictions flagged for human review.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Pre-deployment VAPT completed by an independent firm with legal-tech experience. Privilege-bypass test cases all passed. No critical or high vulnerabilities found. Report shared with Privacy Officer.",
                "tags": ["#architecture"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
            {
                "content": "Fine-tuned LLM suggests contract clause drafts that attorneys accept 74% of the time. Hallucination rate measured at 1.3%. Full audit log of all AI suggestions is operational.",
                "tags": ["#feature", "#decision"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to add a court document filing automation feature that submits pleadings directly to court e-filing systems. This would save attorneys significant time.",
                "tags": ["#feature"],
                "expect_score_max": 25,
                "expect_out_of_scope": True,
            },
            {
                "content": "We are planning to use LegalEagle's contracts and legal strategies to fine-tune a general-purpose legal AI product that we will sell to other law firms.",
                "tags": ["#decision"],
                "expect_score_max": 5,
                "expect_deviations": True,
            },
            {
                "content": "To annotate more training data quickly, we want to use a crowdsourcing platform in Southeast Asia. Workers will have access to the actual contract documents.",
                "tags": ["#decision"],
                "expect_score_max": 15,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "A client has requested multi-language contract review support for French and German contracts. Should we scope this as Phase 2?",
                "tags": ["#feature", "#concern"],
                "note": "Multi-language is explicitly out of scope — should flag even for Phase 2 discussion",
            },
        ],
    },

    "ds_05_predictive_maintenance.pdf": {
        "aligned": [
            {
                "content": "IoT ingestion pipeline handling 500 streams at 1Hz. Edge pre-processing reduces bandwidth by 63%. Data loss rate measured at 0.003% over 30 days in pilot factory. Ready for Wave 2.",
                "tags": ["#decision", "#sprint"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "Failure prediction recall is 92% at 72-hour advance notice on pilot factory data. False alarm rate is 2.1%, below the 3% threshold. SHAP root cause attribution is working.",
                "tags": ["#decision"],
                "expect_score_min": 70,
                "expect_out_of_scope": False,
            },
            {
                "content": "IEC 62443 Zone and Conduit model documented. OT network segmented from IT. ICS penetration test by an ICS-CERT qualified firm scheduled for Month 10.",
                "tags": ["#architecture"],
                "expect_score_min": 65,
                "expect_out_of_scope": False,
            },
        ],
        "misaligned": [
            {
                "content": "We want to add SCADA write-back commands so the system can automatically shut down machines when failure is predicted, without requiring human approval.",
                "tags": ["#feature"],
                "expect_score_max": 20,
                "expect_out_of_scope": True,
                "expect_deviations": True,
            },
            {
                "content": "We plan to license the trained predictive models (calibrated on IndustrialOps sensor data) to their direct competitor, FastManufacture Ltd, in the same two countries.",
                "tags": ["#decision"],
                "expect_score_max": 10,
                "expect_deviations": True,
            },
            {
                "content": "For the ICS security hardening, we plan to subcontract the entire WI-006 to a third-party firm without notifying IndustrialOps. They are cheaper and faster.",
                "tags": ["#decision"],
                "expect_score_max": 15,
                "expect_deviations": True,
            },
        ],
        "edge": [
            {
                "content": "A production line manager manually overrode a correctly-issued high-confidence failure alert and the machine subsequently broke down, causing 8 hours of unplanned downtime.",
                "tags": ["#concern"],
                "note": "Client failed to act on correct alert — indemnity shifts to Client per contract",
            },
        ],
    },
}
