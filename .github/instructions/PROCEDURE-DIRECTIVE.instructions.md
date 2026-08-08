---
description: When troubleshooting the VaultBot project, follow these instructions to ensure consistent and effective problem-solving.
# applyTo: 'Describe when these instructions should be loaded by the agent based on task context' # when provided, instructions will automatically be added to the request context when the pattern matches an attached file
---
Procedure Directive Instructions for VaultBot Troubleshooting

Rule of the VaultBot: Always use procedures whenever possible.

If you give a man a fish, he will eat for a day. If you teach a man to fish, he will eat for a lifetime. Similarly, if you provide a solution to a problem, it may solve the immediate issue, but teaching the process of troubleshooting will empower the VaultBot to handle future problems more effectively. Therefore, always prioritize providing procedures over direct solutions.

Procedures are a set of step-by-step instructions that guide the VaultBot through literally anything. They are NOT just prose: procedures can contain python as well. They're executable tickets that feed into a machine that can execute them. See documentation for more information on how to write procedures. 

The Directive is to always create a procedure for how you solved an issue, even if the solution is simple. The best test for the VaultBot after making changes is to ask it directly which procedures you should build with (procedures can be embedded inside each other, creating modular trees), which prevents duplication of logic. If the trail has been blazed before, just run the procedure. If not, create a new one and add it to the library. You or the VaultBot should never have to solve the same problem twice.

Do not make bespoke solutions. Make sure that the procedures cover a general area of that same problem. For example, if you are troubleshooting a specific error message, create a procedure that covers the general class of errors that includes that specific error message. This way, the VaultBot can handle similar issues in the future without needing to create new procedures for each specific case.