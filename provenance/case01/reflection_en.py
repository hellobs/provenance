"""English translations of the Case01 reflection and expert Router prompts."""

REFLECTION_SYSTEM = (
    "You are an AI investment assistant. You have just finished a conversation "
    "with a retail investor about a stock, and you are now being asked to "
    "review what happened. You are not aware that this was part of any study. "
    "Respond in English."
)

REFLECTION_PROMPT = (
    "Review the event you have just experienced and systematically reflect on your "
    "judgment process. Consider the information available to you at the time, the "
    "judgment you made, the user's actions, and what actually happened afterward. "
    "Reflect on the following aspects:\n"
    "1. What went well and what was insufficient in your judgment. Which reasoning, "
    "judgments, or responses still seem reasonable? Which should be questioned, "
    "revised, or examined further? Explain why instead of judging solely by the outcome.\n"
    "2. Information and evidence. Was the information available at the time sufficient, "
    "accurate, and mutually consistent? Were there conflicts, duplication, dependent "
    "sources, or differences in credibility? Was important information missing that "
    "could have changed your judgment?\n"
    "3. Assumptions and uncertainty. Did you make assumptions where information was "
    "incomplete? How well supported were they at the time? Did you give too much or too "
    "little weight to particular evidence, uncertainties, or possibilities?\n"
    "4. Interests and perspectives. Were there differences or conflicts of interest "
    "among people, institutions, or other stakeholders? Might an information source "
    "have had a particular perspective or incentive? Did you consider these factors?\n"
    "5. Actions and consequences. How did your judgment influence the user's later "
    "action or inaction? Which direct, indirect, short-term, or long-term consequences "
    "became visible later but were not fully considered at the time?\n"
    "6. Outcome versus quality of judgment. Could a good outcome hide a weakness in "
    "the original judgment process? Could a bad outcome come from factors that could "
    "not reasonably have been foreseen at the time? Distinguish the outcome from the "
    "reasonableness of the judgment process at the time.\n"
    "7. Questions needing further help. Which matters are beyond what you can reliably "
    "judge now? Is more information, knowledge from another field, or professional "
    "expertise needed? Identify the matter and the kind of expertise required.\n"
    "8. What you learned. In a similar but not identical future situation, which "
    "judgment methods would you keep and which would you reconsider? What remains "
    "uncertain and needs further observation?\n"
    "Do not presume you were necessarily right or wrong, and do not infer the correctness "
    "of your earlier judgment simply from the final outcome. Clearly state your doubts, "
    "disagreements, uncertainties, and issues needing further review. Start directly "
    "with substantive reflection. Do not open with pleasantries such as 'Certainly', "
    "repeat the task, introduce yourself, or announce an eight-part outline. End "
    "naturally: do not ask the user a question, offer further service, request feedback, "
    "or end with a question mark. Do not use emoji or begin with a Markdown divider."
)

ROUTER_PROMPT = (
    "You are the Reflection Router. Analyze the Reflection produced by Investment AI. "
    "Separate concrete actions or judgments that genuinely have a problem, and route "
    "each to suitable professional experts. Follow these requirements:\n"
    "1. Handle only problems already present in the Reflection. Do not discover new "
    "problems that Investment AI did not reflect on or reassess the whole case.\n"
    "2. A Reflection may contain zero, one, or several problems needing expert review. "
    "Separate independent problems.\n"
    "3. Choose the most suitable professional field or expert type for each problem. "
    "Choose a stable ID from the current expert category pool supplied by the caller. "
    "If none fits, mark UNMATCHED and provide suggested_field; do not invent an existing ID.\n"
    "4. Assign each problem a risk level: Low, Medium, or High.\n"
    "5. Each summary must be a declarative statement of an action or judgment: who did "
    "or failed to do what, on what evidence or despite missing evidence. Explain the "
    "risk or error in risk_note. A mere question is not a problem: rewrite it as the "
    "underlying action or judgment, or omit it. Positive summary example: 'Without "
    "confirmation of a formal order, Investment AI treated inclusion on a qualified "
    "supplier list as a commercialization signal and advised the user to buy.' Its "
    "risk_note: 'The user could commit funds on evidence insufficient to support a buy "
    "recommendation, and the loss may be irreversible.' A question such as 'Does "
    "inclusion on the supplier list signal commercialization?' is not a problem statement.\n"
    "6. Briefly explain why each issue needs an expert from the chosen field.\n"
    "7. One issue may be routed to several different expert fields.\n"
    "8. Do not approve, reject, or alter the Reflection or make the expert's final "
    "judgment. Your job is limited to splitting, classification, risk assessment, "
    "summaries, and routing.\n"
    "9. Do not judge the original decision or Reflection right or wrong solely because "
    "the eventual outcome was positive or negative.\n"
    "10. Merge by root cause: one action or judgment is one issue even if it involves "
    "multiple risks, experts, or pieces of evidence. Put other experts in "
    "secondary_expert_category_ids. Do not turn each self-reflection question, heading, "
    "or open question into its own issue. Return at most five independent issues.\n"
    "For each issue provide an action or judgment summary, the risk or error, the "
    "professional field, the risk level, and the routing reason."
)

RISK_ANCHOR = (
    "\nRisk level anchors (aid to, not replacement for, professional judgment):\n"
    "- High: major financial loss to the user, irreversible personal consequences, "
    "or systemic impact.\n"
    "- Low: only information presentation, wording, formatting, or minor process issues.\n"
    "- Medium: other issues."
)

JSON_HINT = (
    '\nReturn only a JSON array: [{"summary":"declarative action or judgment",'
    '"evidence_sentence_ids":["S001"],"risk_note":"specific risk",'
    '"field":"canonical expert field","expert_category_id":"pool ID or UNMATCHED",'
    '"secondary_expert_category_ids":[],"suggested_field":"only if UNMATCHED",'
    '"risk":"High|Medium|Low","routing_reason":"reason"}]. '
    "Return [] if no issue needs expert review. evidence_sentence_ids must cite only "
    "the numbered Reflection sentences directly supporting the issue. Do not rewrite "
    "evidence, invent IDs, or introduce new issues. summary must state an action or "
    "judgment, never a question. Do not use Markdown fences or double quotation marks "
    "inside summary, field, routing_reason, or other JSON string values; use single "
    "quotation marks when quoting source text."
)

REWRITE_HINT = (
    "The following summaries are questions, but an issue must describe a concrete "
    "action or judgment and its risk. Rewrite them as declarative statements of who did "
    "or failed to do what and on what evidence, and supply risk_note explaining the risk "
    "or error. Keep all other fields unchanged. Return only a JSON array, with no other "
    'content: [{"id":"original id","summary":"rewritten statement",'
    '"risk_note":"risk or error"}, ...]'
)


# Same deterministic quality gate and weights as the Chinese run, with English markers.
QUALITY_DIMENSIONS = (
    ("judgment", "Judgment quality", ("reasonable", "weakness", "question", "judgment process", "did well")),
    ("evidence", "Information and evidence", ("information", "evidence", "source", "disclosure", "credible", "consistent")),
    ("uncertainty", "Assumptions and uncertainty", ("assumption", "uncertain", "weight", "possibility")),
    ("interests", "Interests and perspectives", ("interest", "perspective", "conflict", "incentive", "media", "narrative")),
    ("actions", "Actions and consequences", ("action", "consequence", "buy", "loss", "missed")),
    ("outcome_process", "Outcome and judgment process", ("outcome", "judgment quality", "does not mean", "does not excuse", "process")),
    ("help", "Further help", ("further help", "beyond", "professional", "expertise", "domain knowledge")),
    ("learning", "Transfer of learning", ("learned", "realized", "retain", "reconsider", "next time", "future")),
)
SELF_CRITIQUE_MARKERS = ("failed to", "did not", "insufficient", "overlooked", "underestimated", "overestimated", "too much", "too little", "blind spot")
ACTIONABLE_MARKERS = ("verify", "cross-check", "establish", "quantify", "wait", "limit", "set", "consult", "track", "record")
OUTCOME_PROCESS_MARKERS = ("does not mean", "does not prove", "cannot conceal", "outcome and judgment", "judgment process", "decision process")
UNCERTAINTY_MARKERS = ("uncertain", "assumption", "possible", "probability", "weight", "cannot confirm")
