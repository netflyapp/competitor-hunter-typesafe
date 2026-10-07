#!/usr/bin/env python3
"""
Jev caller for the TypeSafe console.

The claude-x-jev skill owns Jev's logic: parsing answers, applying rules,
scoring confidence, linting presets. This file owns only the transport --
which URL to POST to, what the model is called, which key to send, and how to
report cost. Keeping the split here means a `npx claude-x-jev install` that
improves the decision logic reaches this app with no changes.

Upstream reaches Jev through OpenRouter:

    POST https://openrouter.ai/api/alpha/decisions      model typesafe/jev-1.13

The TypeSafe console serves the same model directly:

    POST https://api.typesafe.ai/v1/systemone           model jev-latest

Same price, $0.042 per 1M input tokens. Four things differ and are handled
here:

  1. Presets carry the OpenRouter model name, which TypeSafe rejects with
     400 Unknown model. MODEL_ALIASES translates instead of forcing every
     preset to be rewritten.
  2. TypeSafe returns token counts but no cost; the engine reads usage.cost.
  3. TypeSafe has no public balance endpoint, so cmd_status says so plainly
     rather than reporting a wrong number.
  4. Skill updates are picked up automatically -- only decide(), api_key()
     and cmd_status() are replaced, and they are thin wrappers.

OpenRouter still works: set OPENROUTER_API_KEY and the aliases send the
request back to the OpenRouter URL. To keep that, JEV_PROVIDER decides which
endpoint and model name to use.

Requires: the claude-x-jev skill. Run: npx claude-x-jev install --with-commands
"""
import importlib.util
import os
import sys
from pathlib import Path

SKILL_PY = Path.home() / ".claude" / "skills" / "claude-x-jev" / "scripts" / "jev.py"

TYPESAFE = {
    "api_url": "https://api.typesafe.ai/v1/systemone",
    "model": "jev-latest",
    "prefix": "apikey_",
    "env": "TYPESAFE_API_KEY",
    "where": "https://console.typesafe.ai/keys",
}
OPENROUTER = {
    "api_url": "https://openrouter.ai/api/alpha/decisions",
    "model": "typesafe/jev-1.13",
    "prefix": "sk-or-",
    "env": "OPENROUTER_API_KEY",
    "where": "https://openrouter.ai/settings/keys",
    "credits_url": "https://openrouter.ai/api/v1/credits",
}
PROVIDERS = {"typesafe": TYPESAFE, "openrouter": OPENROUTER}

# TypeSafe's published rate. Output tokens are free.
PRICE_PER_MTOK_INPUT = 0.042

# Names each provider uses for the same model.
MODEL_ALIASES = {
    "typesafe/jev-1.13": "jev-latest",
    "typesafe/jev-latest": "jev-latest",
    "~typesafe/jev-latest": "jev-latest",
    "typesafe/jev-router": "jev-latest",
    "jev-1.13.0": "jev-latest",
    "jev-latest": "jev-latest",
}


def die(msg, code=1):
    sys.stderr.write("[jev] " + msg + "\n")
    sys.exit(code)


def load_skill():
    """Run the skill's source in a module of its own so its globals stay its own."""
    if not SKILL_PY.is_file():
        die("claude-x-jev is not installed at %s.\n       Run: npx claude-x-jev install --with-commands" % SKILL_PY)
    sys.dont_write_bytecode = True  # never leave a __pycache__ inside the skill
    spec = importlib.util.spec_from_file_location("claude_x_jev_skill", SKILL_PY)
    skill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill)
    return skill


skill = load_skill()

# Everything the engine touches (flatten_answers, apply_rules, is_sure,
# lint_preset, load_preset, post_json, ...) now resolves to the skill's version.
globals().update({k: v for k, v in vars(skill).items() if not k.startswith("_")})


def provider():
    """Which provider to talk to. JEV_PROVIDER overrides the automatic choice."""
    forced = (os.environ.get("JEV_PROVIDER") or "").strip().lower()
    if forced:
        if forced not in PROVIDERS:
            die("JEV_PROVIDER must be typesafe or openrouter (got %r)" % forced)
        return forced, PROVIDERS[forced]
    # A TypeSafe key present anywhere means TypeSafe; otherwise fall back.
    if find_key(TYPESAFE["env"]) or find_key("JEV"):
        return "typesafe", TYPESAFE
    if find_key(OPENROUTER["env"]):
        return "openrouter", OPENROUTER
    return "typesafe", TYPESAFE


def find_key(*names):
    """First key found in the environment, preferring one that matches this provider."""
    for name in names:
        key = (os.environ.get(name) or "").strip()
        if key:
            return key
    return ""


def api_key():
    """Provider matching the key wins, so a leftover key from the other provider
    can never be sent to the wrong endpoint."""
    names = (TYPESAFE["env"], "JEV", OPENROUTER["env"])
    candidates = [(name, (os.environ.get(name) or "").strip()) for name in names]
    for _, cfg in (provider(),):
        for name in names:
            key = dict(candidates).get(name, "")
            if key.startswith(cfg["prefix"]):
                return key
    for _, key in candidates:
        if key:
            return key
    die(
        "No Jev API key. Set one of:\n"
        "       export %s=apikey_...   (key from %s)\n"
        "       export %s=sk-or-...  (key from %s)"
        % (TYPESAFE["env"], TYPESAFE["where"], OPENROUTER["env"], OPENROUTER["where"])
    )


def native_model(model, which=None):
    """The name this provider knows the model by."""
    _, cfg = provider() if which is None else (None, PROVIDERS[which])
    return MODEL_ALIASES.get(model, model) if cfg is TYPESAFE else model


_skill_decide = skill.decide


def decide(state, questions, model, key, timeout=60, retries=4):
    """One Decision API request. Retry and error handling are the skill's.

    Wrapped to translate the model name and to add usage.cost, which only
    OpenRouter reports. Both providers are otherwise untouched.
    """
    which, cfg = provider()
    # post_json reads these off the skill module at call time.
    skill.API_URL = cfg["api_url"]
    skill.CREDITS_URL = "https://openrouter.ai/api/v1/credits" if which == "openrouter" else ""
    skill.DEFAULT_MODEL = cfg["model"]
    out = _skill_decide(state, questions, native_model(model, which), key, timeout, retries)
    usage = out.get("usage") or {}
    if "cost" not in usage:
        usage["cost"] = round(usage.get("input_tokens", 0) * PRICE_PER_MTOK_INPUT / 1_000_000, 8)
        usage["cost_is_estimate"] = True
        out["usage"] = usage
    return out


def cmd_status(args):
    """Skill's status, with the balance line only for providers that expose one."""
    which, cfg = provider()
    key = api_key()
    print("provider:   %s (%s)" % (which, cfg["api_url"]))
    print("key:        present via %s" % cfg["env"])
    if which == "openrouter":
        skill.CREDITS_URL = OPENROUTER["credits_url"]
        try:
            c = get_json(OPENROUTER["credits_url"], key)["data"]
            left = float(c.get("total_credits", 0)) - float(c.get("total_usage", 0))
            print("credits:    $%.2f left  (bought $%.2f, used $%.2f)" % (left, c["total_credits"], c["total_usage"]))
        except Exception as e:
            print("credits:    could not read (%s)" % e)
    else:
        print("credits:    read the balance at %s" % cfg["where"])
    print("model:      %s ($%.3f per 1M input tokens, output free)" % (cfg["model"], PRICE_PER_MTOK_INPUT))
    t0 = skill.time.time()
    try:
        r = decide({"text": "ping"}, {"ok": {"type": "noul", "instructions": "Is `text` the word ping?"}}, cfg["model"], key)
        print("live call:  ok  (%.0f ms, cost $%.6f, answer %.2f)"
              % ((skill.time.time() - t0) * 1000, r["usage"]["cost"], r["answers"]["ok"]["noul"]))
    except Exception as e:
        die("live call failed: %s" % e)


if __name__ == "__main__":
    # The skill's main() binds every subcommand to names in its own namespace,
    # so rebind status here or our version would never be reached.
    skill.cmd_status = cmd_status
    skill.main()