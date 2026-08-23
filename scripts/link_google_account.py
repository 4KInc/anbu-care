"""Link a Google account to a family contact, so that account can sign in.

Signing in proves who someone is. It does not say whose record they may read —
that comes from already being a family contact on the parent. This is the step
that records the link, and without it a perfectly valid Google account gets a
403, which is the system working rather than failing.

    uv run python scripts/link_google_account.py --parent parent-xxxx
    uv run python scripts/link_google_account.py --parent parent-xxxx \
        --contact Karthik --email you@example.com --apply
"""

from __future__ import annotations

import argparse

from anbu_care import service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", required=True)
    parser.add_argument("--contact", help="contact name, or a unique prefix of it")
    parser.add_argument("--email", help="the Google account address")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    profile = service.load_profile(args.parent)
    if profile is None:
        print(f"no profile for {args.parent}")
        return 1

    print(f"{profile.name} — {len(profile.family_contacts)} contact(s)\n")
    for contact in profile.family_contacts:
        linked = contact.email or "— cannot sign in"
        print(f"  {contact.name:24} {contact.whatsapp_e164:16} {linked}")

    if not args.contact or not args.email:
        print("\npass --contact and --email to link one")
        return 0

    wanted = args.contact.strip().lower()
    matches = [c for c in profile.family_contacts
               if c.name.strip().lower().startswith(wanted)]
    if len(matches) != 1:
        print(f"\n{len(matches)} contact(s) match {args.contact!r}; be more specific")
        return 1

    contact = matches[0]
    email = args.email.strip().lower()
    print(f"\n{contact.name}: {contact.email or '(none)'} -> {email}")
    if args.apply:
        contact.email = email
        service.save_profile(profile)
        print("written")
    else:
        print("re-run with --apply to write it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
