# ADR 0008: Keep Domain Values Grouped and Repository Flushes Centralized

## Status

Accepted

## Decision

Keep behavior unchanged while making domain boundaries readable: shared repository flush behavior lives in one helper, and recording fields travel through a value object instead of a long parameter list.

## Consequences

Repository methods no longer repeat session flush plumbing. Recording lifecycle code names its related values together, reducing call-site mistakes and keeping the service interface focused.
