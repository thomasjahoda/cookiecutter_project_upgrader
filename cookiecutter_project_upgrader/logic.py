import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import MutableMapping, Optional, Tuple

import click
from click import ClickException
from cookiecutter.main import cookiecutter


class _TemporaryGitWorktreeDirectory:
    """Context Manager for a temporary working directory of a branch in a git repo"""

    def __init__(self, path: str, repo: str, branch: str = 'master'):
        self.repo = repo
        self.path = path
        self.branch = branch

    def __enter__(self):
        if not os.path.exists(os.path.join(self.repo, ".git")):
            raise Exception("Not a git repository: %s" % self.repo)

        if os.path.exists(self.path):
            raise Exception("Temporary directory already exists: %s" % self.path)

        os.makedirs(self.path)
        subprocess.run(["git", "worktree", "add", "--no-checkout", self.path, self.branch],
                       cwd=self.repo, check=True)

    def __exit__(self, type, value, traceback):
        shutil.rmtree(self.path)
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, check=True)


def _git_repository_has_local_changes(git_repository: Path):
    result: subprocess.CompletedProcess = subprocess.run(["git", "diff-index", "--quiet", "HEAD", "--"],
                                                         cwd=str(git_repository), check=False)
    if result.returncode == 0:
        return False
    elif result.returncode == 1:
        return True
    else:
        raise Exception("could not determine whether git worktree is clean: " + repr(result))


def _git_dir(git_repository: str) -> str:
    """The repository's git directory. In a linked worktree `.git` is a file, not this directory."""
    return subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=git_repository,
                          stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout.strip()


def update_project_template_branch(context: MutableMapping[str, str], project_directory: str, branch: str,
                                   upgrade_branch: Optional[str], merge_now: Optional[bool],
                                   push_template_branch_changes: Optional[bool],
                                   exclude_pathspecs: Tuple[str, ...], interactive: bool):
    """Update template branch from a template url"""
    template_url = context['_template']
    tmp_directory = os.path.join(_git_dir(project_directory), "cookiecutter")
    project_directory_name = os.path.basename(project_directory)
    tmp_git_worktree_directory = os.path.join(tmp_directory, context.get('project_slug') or project_directory_name)

    if subprocess.run(["git", "rev-parse", "-q", "--verify", branch], cwd=project_directory).returncode != 0:
        # create a template branch if necessary
        if subprocess.run(["git", "rev-parse", "-q", "--verify", f"origin/{branch}"],
                          cwd=project_directory).returncode == 0:
            click.echo(f"Created git branch {branch} tracking origin/{branch}")
            subprocess.run(["git", "branch", branch, f"origin/{branch}"],
                           cwd=project_directory,
                           stdout=subprocess.PIPE,
                           universal_newlines=True,
                           check=True)
        else:
            click.echo(f"Creating git branch {branch} ")
            firstref = subprocess.run(["git", "rev-list", "--max-parents=0", "--max-count=1", "HEAD"],
                                      cwd=project_directory,
                                      stdout=subprocess.PIPE,
                                      universal_newlines=True,
                                      check=True).stdout.strip()
            subprocess.run(["git", "branch", branch, firstref], cwd=project_directory)

    with _TemporaryGitWorktreeDirectory(tmp_git_worktree_directory, repo=project_directory, branch=branch):
        # update the template
        click.echo(f"Updating template in branch {branch} using extra_context={context}")
        if upgrade_branch is not None:
            click.echo(f"Updating from project remote branch={upgrade_branch}")
        click.echo("===========================================")
        cookiecutter(template_url,
                     no_input=True,
                     extra_context=context,
                     overwrite_if_exists=True,
                     output_dir=tmp_directory,
                     checkout=upgrade_branch)
        click.echo("===========================================")
        click.echo("Finished generating project from template.")

        # commit to template branch
        subprocess.run(["git", "add", "-A", "."], cwd=tmp_git_worktree_directory, check=True)

        if exclude_pathspecs:
            click.echo("Excluding git pathspecs {}".format(exclude_pathspecs))
            subprocess.run(("git", "reset", "HEAD") + exclude_pathspecs,
                           cwd=tmp_git_worktree_directory, check=True)
            subprocess.run(("git", "checkout") + exclude_pathspecs,
                           cwd=tmp_git_worktree_directory, check=True)

        has_changes = _git_repository_has_local_changes(Path(tmp_git_worktree_directory))
        if has_changes:
            click.echo("Committing changes...")
            subprocess.run(["git", "commit", "-nm", "Update template"],
                           cwd=tmp_git_worktree_directory, check=True)
            push_template_branch_changes = _determine_option(push_template_branch_changes,
                                                             "Push changes to remote branch?",
                                                             interactive)
            if push_template_branch_changes:
                subprocess.run(["git", "push", "origin", branch],
                               cwd=tmp_git_worktree_directory, check=True)
            else:
                click.echo(f"Changes to the branch {branch} have not been pushed to a remote branch.")
            click.echo("===========")

    if has_changes:
        merge_now = _determine_option(merge_now, "Merge changes into current branch?", interactive)
        if merge_now:
            result = subprocess.run(["git", "merge", branch],
                                    cwd=project_directory, check=False)
            click.echo("===========")
            if result.returncode == 0:
                click.echo("Merged changes successfully.")
            else:
                raise ClickException("Started merging changes into current branch, "
                                     "however there seem to be conflicts.")
        else:
            click.echo(
                f"Changes have been commited into branch '{branch}'. "
                f"Use the following command to update your branch:\n"
                f"git merge {branch}")

    else:
        raise ClickException("No changes found")


def _determine_option(current_value: Optional[bool], interactive_question_text: str, interactive: bool):
    if current_value is None:
        if interactive and sys.stdout.isatty():
            current_value = click.confirm(interactive_question_text, default=True)
        else:
            current_value = False
    return current_value
