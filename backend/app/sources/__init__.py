"""Ad-hoc source module.

Profiles an external file (CSV export, Drive scan, legacy dump), matches its
column metadata to the CRM's property metadata (live portal or fake) with an
evidence trail, matches its rows to existing CRM records, and loads the result
into the probe surface (export-contract JSONL), optionally into the fake CRM,
plus an import plan. It never writes to the production portal: the full loader
that attaches recovered records and notes lives in project mir-load; this is
the complement that lets the ruler context and the logic engine reason about an
ad-hoc addition before it exists in the portal.
"""
