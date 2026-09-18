# Recovery Packet Human Sign-off

Status: **pending human approval**. These are evidence-backed recommendations,
not benchmark ground truth. All seven machine-readable rows remain
`human_approved: false`, and live evaluation is blocked before provider
construction.

## Recommended decisions

| Label | Decision | Category | Severity | Evidence summary |
|---|---|---|---|---|
| [Django 3933626526](https://github.com/django/django/pull/21875#discussion_r3933626526) | Confirm | `error_handling` | medium | A missing-module function regressed from a precise “No module” failure to the misleading “Could not find function … in None”; the final revision restored the dedicated branch. |
| [Django 3933613984](https://github.com/django/django/pull/21875#discussion_r3933613984) | Confirm | `regression` | medium | Reusing importability-oriented `qualname()` in admindocs loses human-readable closure names and can make a local view class raise; the final revision reverted the change. |
| [Ansible 3778118024](https://github.com/ansible/ansible/pull/87412#discussion_r3778118024) | Confirm | `regression` | medium | The eager guard changes the established empty-object/empty-accessor result from `[]` to an exception; the later fix removes that unconditional rejection. |
| [Celery 3963659997](https://github.com/celery/celery/pull/10564#discussion_r3963659997) | Exclude | — | — | The alleged hang depends on `threads` plus unsupported termination behavior; the retained smoke test passed after reverting the proposed fix, and no supported failing reproducer exists. |
| [Flask 185170717](https://github.com/pallets/flask/pull/2748#discussion_r185170717) | Exclude | — | — | The exact allegation is false: prefix `/test` plus rule `/` still produces `/test/`. A later empty-rule corner case is a different claim. |
| [Black 3006479284](https://github.com/psf/black/pull/5068#discussion_r3006479284) | Confirm | `error_handling` | medium | The TokenError diagnostic substitutes error text for the source line and positions the caret against that unrelated text; the final code restores the real source line. |
| [Black 3006468789](https://github.com/psf/black/pull/5068#discussion_r3006468789) | Confirm | `error_handling` | medium | The new ParseError path always emits generic `SyntaxError: invalid syntax`, discarding `pe.msg` and misidentifying the exception; the final code reports the specific ParseError message. |

## Scope caveats

- Django 3933613984 is confirmed for the admindocs display-name regression, not
  for the reviewer's incomplete missing-`__module__` theory.
- Ansible 3778118024 is the empty-object plus empty-accessor regression; an empty
  collection with a nonempty accessor did not regress.
- Black 3006479284 is the source/error-text substitution defect, not merely a
  preference for a `TokenError:` prefix.
- Concrete misleading diagnostics are in PRCritiq's explicit `error_handling`
  scope; excluding them under a narrower correctness-only rubric would be an
  undocumented benchmark-policy change.

If this 5-confirm / 2-exclude split is approved, the derived smoke set will
contain 3 pull requests and 5 labels. Record the approval in
`adjudications.jsonl` by setting every reviewed row to `human_approved: true`
and setting `adjudicator` to the stable identity of the approving human. The
strict apply command will then build the certified set; until that happens it
must continue to fail closed.
