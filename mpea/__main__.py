# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI for the MPEA assembler.

Example:
    python -m mpea --goal "Summarize an outage note" --target claude-3.5-sonnet
"""

import argparse
import sys

from mpea.assemble import engineer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Engineer a prompt. Do not execute the draft task.")
    parser.add_argument("--goal", default="", help="What the engineered prompt should make a model do.")
    parser.add_argument("--draft", default="", help="Optional source prompt. Injection lines are stripped.")
    parser.add_argument("--draft-file", default="", help="Read the draft from a UTF-8 file.")
    parser.add_argument("--target", default="", help="Target model id, for example gpt-4o or llama-3.")
    parser.add_argument("--audience", default="", help="Who reads the model's answer.")
    parser.add_argument("--domain", default="", help="Subject-matter domain for the role section.")
    parser.add_argument("--output-format", default="", help="Exact output contract to embed.")
    args = parser.parse_args(argv)

    draft = args.draft
    if args.draft_file:
        with open(args.draft_file, "r", encoding="utf-8") as handle:
            draft = handle.read()

    result = engineer(
        goal=args.goal,
        draft=draft,
        target_llm=args.target,
        audience=args.audience,
        domain=args.domain,
        output_format=args.output_format,
    )
    sys.stdout.write(result.to_markdown())
    if not result.to_markdown().endswith("\n"):
        sys.stdout.write("\n")
    return 2 if result.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
