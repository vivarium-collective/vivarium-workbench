"""The SMS plugin's hosted env-worker launchers (docs/backend-adapters.md §2.2, step P2 "Workers").

viva-api runs the simulator's **prebuilt image** as a worker in its own pod (REFACTOR-PLAN §2A.8, #942), reached
one of two ways:

* **Proxy** (``ENV_WORKER_PROXY_BASE``) -- viva-api holds the worker's socket and relays calls over HTTP, so the
  workbench needs no reachable address. The hosted workbench uses this (``http://api:8000``).
* **Dial-back** (``ENV_WORKER_ADVERTISE_HOST``) -- the worker connects back to this pod.

The core keeps the ``WorkerLauncher`` protocol, ``LocalWorkerLauncher`` and the composition root
(``env_worker_launcher.default_launcher``), which asks ``SmsProvider.worker_launcher()`` -> :func:`hosted_launcher`.
Moved verbatim from ``vivarium_workbench.lib.env_worker_launcher``; the log lines keep that logger name so log
queries that filter on it still match.

The ``.viv-build.json`` read (``_commit_for_workspace``) lives here too: this plugin is the stamp's only writer
(``remote_build_source``) and these launchers are its only readers on the worker path.
"""
from __future__ import annotations

import logging
from pathlib import Path

from vivarium_workbench.lib.env_compat import get_env
from vivarium_workbench.lib.env_worker_client import EnvWorker, EnvWorkerError, EnvWorkerUnavailable
from vivarium_workbench.lib.env_worker_dialback import DialBackError, DialBackListener
from vivarium_workbench.lib.env_worker_launcher import HOSTED_WORKER_ENV, WorkerLauncher

# The name these lines were logged under before the move (step P2), kept so log queries still match.
logger = logging.getLogger("vivarium_workbench.lib.env_worker_launcher")

_PROXY_ENV, _ADVERTISE_ENV = HOSTED_WORKER_ENV

# How long to wait for a worker pod to schedule, pull (usually cached) and dial
# back. Generous and deliberately distinct from the per-call timeout: this covers
# Kubernetes, not the protocol.
REMOTE_START_TIMEOUT = 300.0


class RemoteEnvWorker(EnvWorker):
    """An ``EnvWorker`` whose peer is a Kubernetes Job.

    Overrides ``close`` so ending a session also deletes the Job. Without this a
    worker pod outlives its only client and is reachable by nobody — the leak the
    TTL backstop exists to catch, and which should not be the normal path.
    """

    _job_name: str
    _client: object

    def close(self) -> None:
        try:
            super().close()
        finally:
            try:
                self._client.stop_env_worker(self._job_name)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001 — teardown must not raise into the pool
                logger.warning("env-worker Job %s not deleted: %s", self._job_name, e)


class RemoteWorkerLauncher:
    """Run the simulator's prebuilt image as a worker; it dials back to us.

    ``advertise_host`` is where the worker connects — this pod's IP, which the
    deployment supplies (Downward API). We do not discover it: viva-api would need
    pod-get to look it up, and we already know it.
    """

    kind = "remote"

    def __init__(self, client, *, advertise_host: str, bind_host: str = "0.0.0.0"):
        self._client = client
        self._advertise_host = advertise_host
        self._bind_host = bind_host

    def env_key(self, workspace: str) -> str:
        """The image, named by the commit its build stamp pins.

        NOT the interpreter: the worker runs the simulator image's own Python and
        never sees a path from this filesystem. Keying on the commit also keeps a
        re-materialized workspace from being served by a warm worker still running
        the image it was pinned to before.
        """
        return f"image:{self._require_commit(workspace)}"

    def _require_commit(self, workspace: str) -> str:
        # Hosted requires every served workspace to be image-backed (§2A.8).
        # Shared with ProxyWorkerLauncher: both run the prebuilt image, so both
        # refuse an unstamped workspace identically rather than drifting.
        return _require_commit(workspace)

    def launch(self, workspace: str, *, interpreter: str | None, timeout: float) -> EnvWorker:
        commit = self._require_commit(workspace)
        with DialBackListener(bind_host=self._bind_host) as listener:
            handle = self._client.start_env_worker(
                commit=commit,
                callback_host=self._advertise_host,
                callback_port=listener.port,
                token=listener.token,
            )
            job_name = handle.get("job_name", "")
            try:
                sock = listener.accept(timeout=REMOTE_START_TIMEOUT)
            except DialBackError as e:
                # The pod never called home. Delete it — otherwise it lingers until
                # TTL — and surface the worker's own logs, which say why far better
                # than the timeout does.
                logs = _safe_logs(self._client, job_name)
                _safe_stop(self._client, job_name)
                raise EnvWorkerUnavailable(
                    f"env worker for {commit} did not connect: {e}"
                    + (f"\nworker logs:\n{logs}" if logs else "")
                ) from e

        worker = RemoteEnvWorker.from_socket(sock, workspace, timeout=timeout)
        worker._job_name = job_name
        worker._client = self._client
        logger.info("remote env worker ready for %s (commit %s, job %s)", workspace, commit, job_name)
        return worker


class ProxyEnvWorker:
    """A worker whose socket is held by viva-api, reached over HTTP (plan §C).

    Duck-typed rather than an ``EnvWorker`` subclass, deliberately: ``EnvWorker``
    IS a socket and its framing, and this has neither. What the pool actually
    needs is three methods — ``call``, ``close``, ``alive`` — so that is the
    surface implemented, and inheriting would only drag in state that must never
    be used.

    The message layer is unchanged. Spec §§6-11 are transport-independent by
    design; this is one more transport for the same method catalog.
    """

    def __init__(self, client, job_name: str, workspace: str, *, timeout: float):
        self._client = client
        self._job_name = job_name
        self.workspace = workspace
        self._timeout = timeout
        self._alive = True

    def call(self, method: str, params: dict | None = None,
             *, timeout: float | None = None):
        """Forward one call. viva-api holds the per-worker lock, so the FIFO
        contract is preserved on its side and needs no lock here.

        ``timeout`` overrides the relay's per-worker default for THIS call (so a
        heavy catalog build gets the long budget the pool assigns it), matching
        ``EnvWorker.call``'s contract for the local transport."""
        if not self._alive:
            raise EnvWorkerUnavailable(f"relayed worker {self._job_name} is closed")
        try:
            resp = self._client.call_relayed_env_worker(
                self._job_name, method=method, params=params,
                timeout=timeout if timeout is not None else self._timeout)
        except Exception as e:  # noqa: BLE001 — normalized below
            # A relayed worker that has gone away must look to the pool exactly
            # like a dead local one, or the pool will keep handing it out. 404
            # (never registered) and 410 (socket dropped) both mean that; any
            # other failure is a transport fault and is reported as unavailable
            # too, because we cannot tell whether the call ran.
            self._alive = False
            raise EnvWorkerUnavailable(f"relayed call {method!r} failed: {e}") from e
        return (resp or {}).get("result")

    #: This transport can run a call as a durable TASK rather than a held
    #: request. Only the proxy can: viva-api owns the socket, so viva-api is the
    #: only party able to keep working after the client stops waiting. A local
    #: subprocess and a dial-back worker are both held by the workbench, so
    #: there is nowhere for a task record to live.
    supports_tasks = True

    #: How long to wait for a task to finish, and how often to ask. Generous
    #: because the work is a study: the point of the task tier is that nothing
    #: is held open, so a long wait costs a poll every few seconds rather than a
    #: socket. Distinct from ENV_WORKER_CALL_TIMEOUT, which bounds a single
    #: interactive request.
    TASK_POLL_INTERVAL = 5.0
    TASK_TIMEOUT = 24 * 3600.0

    def call_task(self, method: str, params: dict | None = None):
        """Run one call as a durable task: submit, poll, return its result.

        The caller still waits — this is not an async API to the workbench — but
        what it waits on is a sequence of short status reads rather than one
        socket held open for hours. That difference is the whole fix: the socket
        timeout that used to fire mid-study, and cause the pool to re-run it,
        has nothing to fire on.

        A failure here is reported, never retried. The task record is the
        authority on what happened, and it survives this process.
        """
        import time

        if not self._alive:
            raise EnvWorkerUnavailable(f"relayed worker {self._job_name} is closed")
        try:
            submitted = self._client.submit_env_worker_task(
                self._job_name, method=method, params=params)
        except Exception as e:  # noqa: BLE001 — normalized for the pool
            self._alive = False
            raise EnvWorkerUnavailable(f"could not submit {method!r} as a task: {e}") from e

        task_id = (submitted or {}).get("task_id")
        if task_id is None:
            raise EnvWorkerUnavailable(
                f"viva-api accepted {method!r} but returned no task_id")

        deadline = time.monotonic() + self.TASK_TIMEOUT
        while True:
            time.sleep(self.TASK_POLL_INTERVAL)
            try:
                task = self._client.get_env_worker_task(int(task_id))
            except Exception as e:  # noqa: BLE001
                # A lost poll is not a lost task: the record is durable, so say
                # what is true -- we stopped watching -- and name the id so it
                # can be looked up rather than presumed dead.
                raise EnvWorkerUnavailable(
                    f"lost track of task {task_id} ({method}): {e}. "
                    f"The work may still be running; check the task, not runs.db."
                ) from e
            status = (task.get("status") or "").lower()
            if status == "completed":
                return task.get("result")
            if status in ("failed", "cancelled", "timeout"):
                raise EnvWorkerError(
                    task.get("error_message") or f"task {task_id} ended {status}")
            if time.monotonic() > deadline:
                raise EnvWorkerUnavailable(
                    f"task {task_id} ({method}) still {status} after "
                    f"{self.TASK_TIMEOUT:.0f}s; it was NOT cancelled and may "
                    f"still be running -- check or cancel it explicitly."
                )

    def alive(self) -> bool:
        return self._alive

    def close(self) -> None:
        """Drop the connection and delete the Job. Idempotent, never raises."""
        self._alive = False
        try:
            self._client.stop_relayed_env_worker(self._job_name)
        except Exception as e:  # noqa: BLE001 — teardown must not raise into the pool
            logger.warning("relayed env-worker Job %s not deleted: %s", self._job_name, e)


class ProxyWorkerLauncher:
    """Ask viva-api to run the image AND hold the socket; talk to it over HTTP.

    The third transport (plan §C/§C1). It exists because a laptop cannot be
    dialled: the SSM tunnel is laptop-initiated with no inbound path, so
    ``RemoteWorkerLauncher``'s dial-back — which requires an address the worker
    can reach — has nothing to advertise. Here the workbench binds no listener
    at all and needs no reachable address, only a URL it can call out to.

    ``kind`` differs from ``"remote"`` on purpose. The pool keys warm workers on
    ``(workspace, env_key, kind)``, and a dial-back worker and a relayed one are
    reached by different means even when they run the identical image; sharing a
    pool entry would hand a caller a handle it cannot use.
    """

    kind = "proxy"

    def __init__(self, client, *, session_key: str | None = None):
        self._client = client
        self._session_key = session_key

    def env_key(self, workspace: str) -> str:
        """Same key as the remote launcher: the image IS the environment.

        Identical by design — the environment does not change because the bytes
        take a different route to it. ``kind`` is what separates the two in the
        pool, and that is the honest place for the distinction.
        """
        return f"image:{_require_commit(workspace)}"

    def launch(self, workspace: str, *, interpreter: str | None, timeout: float) -> ProxyEnvWorker:
        commit = _require_commit(workspace)
        handle = self._client.start_relayed_env_worker(
            commit=commit, session_key=self._session_key,
            accept_timeout=REMOTE_START_TIMEOUT)
        job_name = (handle or {}).get("job_name", "")
        if not job_name:
            raise EnvWorkerUnavailable(
                f"viva-api accepted the relay start for {commit} but returned no job_name")
        logger.info("relayed env worker ready for %s (commit %s, job %s)", workspace, commit, job_name)
        return ProxyEnvWorker(self._client, job_name, workspace, timeout=timeout)


def _require_commit(workspace: str) -> str:
    """The commit whose image IS this workspace's environment.

    Shared by the remote and proxy launchers: both run the prebuilt image, and
    both must refuse a workspace with no build stamp for the same reason.
    """
    commit = _commit_for_workspace(workspace)
    if not commit:
        raise EnvWorkerUnavailable(
            f"workspace {workspace} has no build stamp (.viv-build.json); a hosted "
            "env worker runs the simulator's prebuilt image and needs its commit"
        )
    return commit


def _commit_for_workspace(workspace: str) -> str | None:
    """The commit a materialized build was stamped with.

    ``materialize_build`` writes ``.viv-build.json``; that stamp is the link from a
    served workspace to the image that IS its environment.
    """
    import json

    stamp = Path(workspace) / ".viv-build.json"
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    commit = data.get("commit")
    return str(commit) if commit else None


def _safe_logs(client, job_name: str) -> str | None:
    try:
        return client.env_worker_status(job_name, include_logs=True).get("logs")
    except Exception:  # noqa: BLE001 — diagnostics must never mask the real error
        return None


def _safe_stop(client, job_name: str) -> None:
    try:
        client.stop_env_worker(job_name)
    except Exception:  # noqa: BLE001
        logger.warning("could not delete env-worker Job %s", job_name)


def hosted_launcher() -> WorkerLauncher | None:
    """This deployment's hosted launcher, read from its env, or ``None`` when it configures none (the core then runs
    workers as local subprocesses). No network call. Moved verbatim from the core's ``default_launcher``."""
    from vivarium_workbench_sms.sms_api_client import SmsApiClient, sms_api_base

    # PROXY first (plan §C/§C1). It is the only transport that works where the
    # workbench cannot be dialled — a laptop behind an SSM tunnel — so a
    # deployment that sets it means it, and it must not be shadowed by an
    # ADVERTISE_HOST left over from the dial-back configuration.
    #
    # Both are read so a site can be switched either way without a cross-repo
    # release window in which env workers are broken: viva-api's relay ships
    # inert, this flag turns it on, and unsetting it returns to dial-back with no
    # redeploy of either image.
    proxy_base = (get_env(_PROXY_ENV, "") or "").strip()
    if proxy_base:
        base = proxy_base if proxy_base.lower() not in ("1", "true", "yes") else sms_api_base()
        logger.info("env workers: PROXY (relayed through viva-api at %s)", base)
        return ProxyWorkerLauncher(SmsApiClient(base))

    host = get_env(_ADVERTISE_ENV, "") or ""
    if not host.strip():
        return None

    # The SAME accessor every other viva-api call site uses (VIVA_API_BASE, else
    # SMS_API_BASE). Constructing SmsApiClient() bare would silently take its
    # localhost:8080 default — which in a pod is the workbench itself, not the
    # api Service, so every worker launch would fail to reach viva-api.
    base = sms_api_base()
    logger.info("env workers: REMOTE (image-as-worker), dial-back to %s, api at %s", host, base)
    return RemoteWorkerLauncher(SmsApiClient(base), advertise_host=host.strip())
