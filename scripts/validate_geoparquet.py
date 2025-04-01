import argparse
import sys

# Basic placeholder script for validating a GeoParquet file


def validate_geoparquet(input_file: str, schema_path: str | None):
    print(f"--- Validating GeoParquet --- ")
    print(f"Input file: {input_file}")
    if schema_path:
        print(f"Schema path: {schema_path}")
    else:
        print("Schema path: Not provided")

    # TODO: Add actual validation logic
    # - Check if file exists and is readable
    # - Try reading with geopandas
    # - Check CRS matches expected
    # - Check geometry column exists and is valid
    # - If schema provided, load schema and validate columns, dtypes

    print("Validation checks (placeholder) passed.")
    # If validation fails, exit with a non-zero code:
    # print("ERROR: Validation failed!", file=sys.stderr)
    # sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate GeoParquet file.")
    parser.add_argument(
        "--input-file", required=True,
        help="Path to the input GeoParquet file.")
    parser.add_argument(
        "--schema", default=None,
        help="Optional path to a validation schema (e.g., JSON).")

    args = parser.parse_args()

    validate_geoparquet(args.input_file, args.schema)
