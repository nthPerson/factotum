## Task: split the attached task into steps

The attached task is a goal. Read it with `task_get` and its existing steps with `goal_tree`. If its text
already lists steps, make one task for each listed step that has no task yet, in the order the text
gives; otherwise draft between two and six steps that together finish it, in order. Propose each as a
`create` with a short name (at most 80 characters), the goal's project, an effort when you can tell, a
`ref` (`s1`, `s2`, …) and `links: [{"kind": "part_of", "other": <the goal's id>}]`; add
`{"kind": "waits_on", "other": "<the ref of an earlier step>"}` only where a step truly cannot start
before that one. The `ref` and the `links` go inside `fields`, beside the name:
`{"op": "create", "fields": {"name": "…", "ref": "s1", "links": [{"kind": "part_of", "other": 412}]}}`.
Never a due date unless the text gives one. A step that already exists as a task is
not proposed again; if it is not linked to the goal, propose `link` with `kind` `part_of` instead. A goal
that is itself a step of another task cannot have steps: say so and propose nothing. Reply with the
steps as a short numbered list.
