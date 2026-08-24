# Public command handlers are reached through argparse and package entry points.

# zipfile.ZipInfo exposes these writable fields dynamically; Vulture sees the
# assignments but cannot see the standard-library consumer that reads them.
_.compress_type  # unused attribute (tools/build_zipapp.py:17)
_.external_attr  # unused attribute (tools/build_zipapp.py:18)
