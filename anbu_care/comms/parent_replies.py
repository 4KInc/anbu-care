"""The sentences she gets straight back, in the language she reads.

Every other outbound message is rendered by the model on the way out. These
cannot be: they are the webhook's own response, and Twilio abandons a webhook
at roughly fifteen seconds. A translation call inside that window is a coin
flip, and it has already come up tails once today - `translation unavailable:
504 DEADLINE_EXCEEDED` in the logs, on the one path that must always answer.

So the handful of fixed replies are translated ahead of time and chosen by her
language at request time, which costs nothing. There are only three of them
because only three are fixed: everything with a name or a hospital in it is
composed per case and still goes down the ordinary rendered path.

The day number is the single substitution, and it is the same number the
English carries. Nothing here is translated at request time, so nothing here
can be late, and nothing here can invent a sentence: an unknown language falls
back to English rather than to an approximation of it.
"""

from __future__ import annotations

NOTED = "noted"
RECORDED = "recorded"
STOPPED = "stopped"

# Tamil written to match the register of the check-in she is answering: plain,
# unhurried, and making no claim about how she is.
_TEXT: dict[str, dict[str, str]] = {
    "en": {
        NOTED: "Thanks, that's noted.",
        RECORDED: ("Thanks. Your answer is recorded against today's check-in, "
                   "day {day}. Nobody has assessed it."),
        STOPPED: ("Understood. We have stopped the daily check-in messages. "
                  "Your record is unchanged and you can still message here any "
                  "time. If something is wrong, call 108."),
    },
    "ta": {
        NOTED: "நன்றி. இது பதிவு செய்யப்பட்டது.",
        RECORDED: ("நன்றி. உங்கள் பதில் இன்றைய நலவிசாரிப்புக்கு பதிவு "
                   "செய்யப்பட்டது, {day} வது நாள். யாரும் அதை மதிப்பீடு "
                   "செய்யவில்லை."),
        STOPPED: ("சரி. தினசரி நலவிசாரிப்பு செய்திகளை நிறுத்திவிட்டோம். "
                  "உங்கள் பதிவு அப்படியே இருக்கிறது, எப்போது வேண்டுமானாலும் "
                  "இங்கு செய்தி அனுப்பலாம். ஏதேனும் சரியில்லை என்றால், 108 ஐ "
                  "அழைக்கவும்."),
    },
}


def text(key: str, language: str = "en", **params) -> str:
    """The sentence in her language, or the English if there is no translation.

    Falling back to English is the honest failure. A language this does not
    hold is a language nobody has written these sentences in, and guessing at
    one would put words nobody checked into a message about her health.
    """
    table = _TEXT.get((language or "en").strip().lower(), _TEXT["en"])
    return table.get(key, _TEXT["en"][key]).format(**params)
