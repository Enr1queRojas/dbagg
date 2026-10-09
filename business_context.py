"""Compatibility entry point for validating business context."""

from dbagg.context.loader import load_business_context, main

__all__ = ["load_business_context"]

if __name__ == "__main__":
    main()
