
import pathlib


def test_the_env_var_separator_appears_in_no_value():
    """The deploy died on this. The separator was ^@^ and an email address
    contains @, so ANBU_DEMO_FAMILY_EMAIL split mid-value and gcloud rejected
    the entire flag: "Bad syntax for dict arg: [blockintelai.com]".

    It fails loudly, which is the good case — but only after a build. This
    checks the shape instead: the separator must be a character that cannot
    occur in any value being passed. Emails have @, base64 keys have + / and =,
    phone numbers have +, URLs have : . and -.
    """
    import re

    script = (pathlib.Path(__file__).resolve().parents[1]
              / "infra" / "deploy_cloud_run.sh").read_text()
    match = re.search(r'--set-env-vars "\^(.+?)\^(.+?)"', script, re.DOTALL)
    assert match, "no --set-env-vars flag with a custom separator"

    separator = match.group(1)
    assert separator not in "@+/=:.-", (
        f"separator {separator!r} can occur inside an email, a base64 key, "
        f"a phone number or a URL")

    # And every pair still parses: the separator splits it into NAME=VALUE.
    for pair in match.group(2).split(separator):
        assert "=" in pair, f"{pair!r} is not a NAME=VALUE pair"
