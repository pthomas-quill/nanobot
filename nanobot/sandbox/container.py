import asyncio
import os
from typing import Any
from pathlib import Path
import re
import yaml

from loguru import logger

from nanobot.sandbox.base import Sandbox, ShellResult
from nanobot.config.loader import get_config_path


class ContainerBox(Sandbox):
    """Executes code directly on the host system."""

    def __init__(
        self,
        workspace: Path,
        *args,
        image: str = "nanobot_sandbox",
        backend: str = "podman",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.workspace = Path(workspace).expanduser().resolve()
        self.full_image_name = f"nanobot/{image.strip()}"
        self.backend = backend.lower()
        assert self.backend in (
            "docker",
            "podman",
        ), f"Unsupported container backend {self.backend}"
        self.packages_path = (
            get_config_path().parent / "container_packages.yaml"
        ).resolve()
        if not self.packages_path.exists():
            self.packages_path.write_text(
                yaml.safe_dump({"apt": [], "brew": [], "npm": [], "pip": []})
            )
        try:
            self.build_image()
            logger.error("Error building container image: {}", e)
        except Exception:
            # If image build fails, we restore the default image to avoid repeated build attempts and log the error
            try:
                self.build_image(
                    packages={}
                )  # Attempt to build with default packages (which should succeed)
            except Exception as e:
                logger.error(
                    "Failed to restore default container image after build failure: {}",
                    e,
                )
                raise RuntimeError(
                    f"Failed to build container image and failed to restore default image: {e}"
                )

    def build_image(self, packages: dict[str, list[str]] | None = None) -> str | None:
        import subprocess

        dockerfile = self.dockerfile_template
        # add installed packages to dockerfile
        if packages is None:
            try:
                packages = yaml.safe_load(self.packages_path.read_text())
            except Exception as e:
                logger.error("Error reading packages file: {}", e)
                raise RuntimeError(f"Error reading packages file: {e}")

        if packages.get("apt"):
            dockerfile += f"\nRUN apt-get update && apt-get install -y --no-install-recommends {' '.join(packages['apt'])}\n"
        if packages.get("brew"):
            dockerfile += f"\nRUN /home/linuxbrew/.linuxbrew/bin/brew install {' '.join(packages['brew'])}\n"
        if packages.get("npm"):
            dockerfile += f"\nRUN /home/linuxbrew/.linuxbrew/opt/node@24/bin/npm install -g {' '.join(packages['npm'])}\n"
        if packages.get("pip"):
            dockerfile += f"\nRUN pip install {' '.join(packages['pip'])}\n"

        try:
            logger.info("Building container image with packages: {}", packages)
            from tempfile import NamedTemporaryFile

            with NamedTemporaryFile("w", delete=True) as tmp:
                tmp.write(dockerfile)
                tmp.flush()
                tmpfile = tmp.name
                subprocess.run(
                    [
                        self.backend,
                        "build",
                        "--force-rm",
                        "-t",
                        self.full_image_name,
                        "-f",
                        tmpfile,
                    ],
                    check=True,
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )
                subprocess.run(
                    [
                        self.backend,
                        "image",
                        "prune",
                    ],
                    check=True,
                    input="y\n".encode("utf-8"),
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )
            logger.info("Container image built successfully")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error building container image: {e}")

    def add_packages(self, manager: str, packages: str | list[str]) -> None:
        if isinstance(packages, str):
            packages = [packages]
        manager = manager.lower()
        if manager not in ("apt", "brew", "npm", "pip"):
            raise ValueError(f"Unsupported package manager: {manager}")
        try:
            data = yaml.safe_load(self.packages_path.read_text())
        except Exception as e:
            raise RuntimeError(f"Error reading packages file: {e}")
        existing = set(data.get(manager, []))
        existing.update(packages)
        data[manager] = sorted(existing)
        self.build_image(packages=data)

        self.packages_path.write_text(yaml.safe_dump(data))

    def remove_packages(self, manager: str, packages: str | list[str]) -> None:
        if isinstance(packages, str):
            packages = [packages]
        manager = manager.lower()
        if manager not in ("apt", "brew", "npm", "pip"):
            raise ValueError(f"Unsupported package manager: {manager}")
        try:
            data = yaml.safe_load(self.packages_path.read_text())
        except Exception as e:
            raise RuntimeError(f"Error reading packages file: {e}")
        existing = set(data.get(manager, []))
        existing.difference_update(packages)
        data[manager] = sorted(existing)
        self.build_image(packages=data)

        self.packages_path.write_text(yaml.safe_dump(data))

    def list_packages(self, manager: str | None = None) -> list[str]:
        try:
            data = yaml.safe_load(self.packages_path.read_text())
        except Exception as e:
            raise RuntimeError(f"Error reading packages file: {e}")

        if not manager:
            return data

        manager = manager.lower()
        if manager not in ("apt", "brew", "npm", "pip"):
            raise ValueError(f"Unsupported package manager: {manager}")
        return data.get(manager, [])

    @property
    def dockerfile_template(self) -> str:
        return f"""FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl git ca-certificates

# Install Homebrew
RUN /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
RUN echo >> /root/.bashrc
RUN echo 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv bash)"' >> /root/.bashrc

# Install Node.js using Homebrew
RUN /home/linuxbrew/.linuxbrew/bin/brew install node@24
RUN echo 'export PATH="/home/linuxbrew/.linuxbrew/opt/node@24/bin:$PATH"' >> /root/.bashrc
"""

    def _container_cmd(self, working_dir, command):
        return [
            self.backend,
            "run",
            "--rm",
            # workspace mount
            "-v",
            f"{str(self.workspace)}:{str(self.workspace)}",
            "-w",
            f"{str(working_dir)}",
            self.full_image_name,
            "bash",
            "-lc",
            command,
        ]

    async def execute(
        self, command: str, working_dir: str | None = None, **kwargs: Any
    ) -> str:
        if working_dir is None:
            cwd = self.workspace
        else:
            cwd = self._resolve_path(working_dir, self.workspace, None)

        guard_error = self._guard_command(command)
        if guard_error:
            return ShellResult(stdout="", stderr=guard_error, returncode=-1)

        process = await asyncio.create_subprocess_exec(
            *self._container_cmd(cwd, command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            # Wait for the process to fully terminate so pipes are
            # drained and file descriptors are released.
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            return ShellResult(
                stdout="",
                stderr=f"Error: Command timed out after {self.timeout} seconds",
                returncode=-1,
            )

        return ShellResult(
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
        )

    def is_isolated(self) -> bool:
        return True
