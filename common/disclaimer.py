"""The disclaimer and the non-affiliation statement, used in the README, every
report, and the web app.

Change the wording here only, then copy it into the README. The texts were
reviewed by counsel; do not reword them to match the house style.
"""

DISCLAIMER = (
    'This tool is provided free and "as is," without warranty of any kind, under '
    "the Apache License 2.0. It scans for known patterns associated with the "
    "February 2027 Civis Platform update (Python 3.15 and civis-python v3). It is "
    "not a complete audit. It can miss affected code and can flag code that is not "
    "affected. Results depend on the permissions of the Civis user the scan runs "
    "as; objects that user cannot see are not scanned. Test every job before "
    "February 2027. Expedition Data LLC is not liable for any damages arising from "
    "use of this tool and has no obligation to provide support or updates. This is "
    "not professional advice."
)

NON_AFFILIATION = (
    "This tool is independent and is not affiliated with, endorsed by, sponsored "
    'by, or supported by Civis Analytics. "Civis" and "Civis Platform" are '
    "trademarks of Civis Analytics, used here only to identify the platform this "
    "tool works with."
)
