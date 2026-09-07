"""Advanced asynchronous task processor with performance optimization for Novel Forge.

This module provides an optimized async task processing system with sophisticated
concurrency control, batching, and performance monitoring.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from asyncio import Queue, Task
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    TypeVar,
    cast,
)
from uuid import uuid4

from novel_forge.core.infra.advanced_cache import CacheEvictionPolicy, cache_manager
from novel_forge.core.infra.performance_monitor import (
    get_optimized_thread_pool,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class TaskPriority(Enum):
    """Task priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class TaskConfig:
    """Configuration for task execution."""
    timeout: float = 30.0
    retries: int = 3
    backoff_factor: float = 1.1
    priority: TaskPriority = TaskPriority.NORMAL
    max_concurrent: int = 10
    batch_size: int = 100
    cache_result: bool = False
    cache_ttl: float = 3600.0  # 1 hour


@dataclass
class QueuedTask:
    """Represents a queued task with metadata."""
    id: str
    func: Callable[..., Awaitable[Any]]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    config: TaskConfig
    priority: TaskPriority
    created_at: float = field(default_factory=time.time)
    submitted_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[Exception] = None
    retry_count: int = 0


class AsyncTaskProcessor:
    """Advanced asynchronous task processor with performance optimization."""
    
    def __init__(
        self,
        max_workers: int = 10,
        queue_size: int = 1000,
        enable_monitoring: bool = True,
        cache_results: bool = True,
    ):
        self._max_workers = max_workers
        self._queue_size = queue_size
        self._enable_monitoring = enable_monitoring
        self._cache_results = cache_results
        
        # Priority queues for different priority levels
        self._high_priority_queue: Queue[QueuedTask] = Queue()
        self._normal_priority_queue: Queue[QueuedTask] = Queue()
        self._low_priority_queue: Queue[QueuedTask] = Queue()
        
        # Task tracking
        self._tasks: Dict[str, QueuedTask] = {}
        self._running_tasks: Dict[str, Task[Any]] = {}
        self._running_tasks_lock = Lock()  # Lock for thread-safe access to _running_tasks
        self._worker_tasks: List[Task[Any]] = []
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        
        # Thread pool for CPU-bound tasks
        self._thread_pool = get_optimized_thread_pool(max_workers=max_workers)
        
        # Performance tracking
        self._stats = {
            'submitted': 0,
            'completed': 0,
            'failed': 0,
            'retried': 0,
            'avg_duration': 0.0,
        }
        
        # Shutdown flag
        self._shutdown = False
        
        # Cache for results if enabled
        self._result_cache = cache_manager.get_cache(
            name="task_results",
            max_size=500,
            ttl=3600.0,
            policy=CacheEvictionPolicy.LRU
        ) if cache_results else None
    
    def _owner_loop_or_raise(self) -> asyncio.AbstractEventLoop:
        if self._owner_loop is None:
            raise RuntimeError("AsyncTaskProcessor is not started")
        if self._owner_loop.is_closed():
            raise RuntimeError("AsyncTaskProcessor owner loop is closed")
        if not self._owner_loop.is_running():
            raise RuntimeError("AsyncTaskProcessor owner loop is not running")
        return self._owner_loop

    async def _run_on_owner_loop(self, coro: Coroutine[Any, Any, R]) -> R:
        owner_loop = self._owner_loop_or_raise()
        concurrent_future = asyncio.run_coroutine_threadsafe(coro, owner_loop)
        return await asyncio.wrap_future(concurrent_future)

    async def start(self) -> None:
        """Start the task processor workers."""
        if self._worker_tasks:
            return  # Already started

        current_loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = current_loop
        elif self._owner_loop is not current_loop:
            raise RuntimeError("AsyncTaskProcessor must be started on its owner event loop")

        self._shutdown = False
        for i in range(self._max_workers):
            worker_coro = self._worker(f"worker-{i}")
            worker_task = asyncio.create_task(
                worker_coro,
                name=f"async-task-worker-{i}"
            )
            self._worker_tasks.append(worker_task)

    async def _stop_local(self) -> None:
        self._shutdown = True

        # Cancel all worker tasks
        for task in self._worker_tasks:
            task.cancel()

        # Wait for all workers to finish
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()

        # Cancel all running tasks with lock protection
        with self._running_tasks_lock:
            running_tasks_snapshot = list(self._running_tasks.values())
            self._running_tasks.clear()

        for task in running_tasks_snapshot:
            task.cancel()

        # Wait for running tasks to finish
        if running_tasks_snapshot:
            await asyncio.gather(*running_tasks_snapshot, return_exceptions=True)

        self._owner_loop = None
        self._tasks.clear()
        # Shutdown thread pool in non-blocking mode so desktop close cannot
        # hang when a worker thread is still unwinding a long-running call.
        try:
            self._thread_pool.shutdown(wait=False)
        except Exception:
            logger.debug("AsyncTaskProcessor thread pool shutdown warning", exc_info=True)

    async def stop(self) -> None:
        """Stop the task processor and clean up resources."""
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            await self._run_on_owner_loop(self._stop_local())
            return
        await self._stop_local()
    
    async def _submit_local(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        config: Optional[TaskConfig] = None,
        **kwargs: Any
    ) -> str:
        """Submit a task for execution."""
        if self._shutdown:
            raise RuntimeError("Task processor is shutting down")
        
        task_id = str(uuid4())
        config = config or TaskConfig()
        
        queued_task = QueuedTask(
            id=task_id,
            func=func,
            args=args,
            kwargs=kwargs,
            config=config,
            priority=config.priority,
            submitted_at=time.time()
        )
        
        # Add to appropriate priority queue
        if config.priority == TaskPriority.HIGH:
            await self._high_priority_queue.put(queued_task)
        elif config.priority == TaskPriority.LOW:
            await self._low_priority_queue.put(queued_task)
        else:
            await self._normal_priority_queue.put(queued_task)
        
        self._tasks[task_id] = queued_task
        self._stats['submitted'] += 1
        
        return task_id

    async def submit(
        self,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        config: Optional[TaskConfig] = None,
        **kwargs: Any
    ) -> str:
        """Submit a task for execution."""
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            return await self._run_on_owner_loop(self._submit_local(func, *args, config=config, **kwargs))
        return await self._submit_local(func, *args, config=config, **kwargs)
    
    async def submit_batch(
        self,
        tasks: List[tuple[Callable[..., Awaitable[Any]], tuple[Any, ...], dict[str, Any]]],  # (func, args, kwargs)
        config: Optional[TaskConfig] = None
    ) -> List[str]:
        """Submit multiple tasks for execution."""
        task_ids = []
        for func, args, kwargs in tasks:
            task_id = await self.submit(func, *args, config=config, **kwargs)
            task_ids.append(task_id)
        return task_ids
    
    async def _get_result_local(self, task_id: str, timeout: float = 30.0) -> Any:
        """Get the result of a submitted task."""
        if task_id not in self._tasks:
            raise ValueError(f"Task {task_id} not found")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            task = self._tasks[task_id]
            if task.completed_at is not None:
                if task.error:
                    raise task.error
                return task.result
            
            await asyncio.sleep(0.1)
        
        raise TimeoutError(f"Task {task_id} timed out after {timeout} seconds")

    async def get_result(self, task_id: str, timeout: float = 30.0) -> Any:
        """Get the result of a submitted task."""
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            return await self._run_on_owner_loop(self._get_result_local(task_id, timeout))
        return await self._get_result_local(task_id, timeout)
    
    async def _get_queue_size_local(self) -> int:
        """Get the total number of queued tasks."""
        return (
            self._high_priority_queue.qsize() +
            self._normal_priority_queue.qsize() +
            self._low_priority_queue.qsize()
        )

    async def get_queue_size(self) -> int:
        """Get the total number of queued tasks."""
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            return await self._run_on_owner_loop(self._get_queue_size_local())
        return await self._get_queue_size_local()

    async def _get_stats_local(self) -> Dict[str, Any]:
        """Get performance statistics."""
        with self._running_tasks_lock:
            running_tasks_count = len(self._running_tasks)

        return {
            **self._stats.copy(),
            'queue_size': await self._get_queue_size_local(),
            'running_tasks': running_tasks_count,
            'total_tasks': len(self._tasks),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        if self._owner_loop is not None and self._owner_loop is not asyncio.get_running_loop():
            return await self._run_on_owner_loop(self._get_stats_local())
        return await self._get_stats_local()

    async def _worker(self, worker_id: str) -> None:
        """Worker coroutine that processes tasks from the queue."""
        while not self._shutdown:
            try:
                # Check high priority queue first, then normal, then low
                queued_task = await self._get_next_task()
                if queued_task is None:
                    continue
                
                await self._execute_task(queued_task)
                
            except asyncio.CancelledError:
                logger.info(f"Worker {worker_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")
                await asyncio.sleep(1)  # Brief pause before continuing
    
    async def _get_next_task(self) -> Optional[QueuedTask]:
        """Get the next task based on priority."""
        # Check high priority first
        if not self._high_priority_queue.empty():
            return await self._high_priority_queue.get()
        
        # Then normal priority
        if not self._normal_priority_queue.empty():
            return await self._normal_priority_queue.get()
        
        # Finally low priority
        if not self._low_priority_queue.empty():
            return await self._low_priority_queue.get()
        
        # No tasks available, wait briefly
        await asyncio.sleep(0.01)
        return None
    
    async def _execute_task(self, queued_task: QueuedTask) -> None:
        """Execute a single task with retry logic."""
        task_id = queued_task.id
        queued_task.started_at = time.time()
        
        # Check if result is cached
        cache = self._result_cache if (self._cache_results and queued_task.config.cache_result) else None
        cache_key: Optional[str] = None
        if cache is not None:
            cache_key = f"{task_id}:{hash(str(queued_task.args))}:{hash(str(sorted(queued_task.kwargs.items())))}"
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                queued_task.result = cached_result
                queued_task.completed_at = time.time()
                self._stats['completed'] += 1
                return
        
        # Execute with retry logic
        last_error = None
        for attempt in range(queued_task.config.retries + 1):
            try:
                if attempt > 0:
                    self._stats['retried'] += 1
                    # Exponential backoff
                    await asyncio.sleep(
                        queued_task.config.backoff_factor ** attempt
                    )
                
                # Execute the task with timeout
                try:
                    result = await asyncio.wait_for(
                        queued_task.func(*queued_task.args, **queued_task.kwargs),
                        timeout=queued_task.config.timeout
                    )
                    
                    queued_task.result = result
                    queued_task.completed_at = time.time()
                    
                    # Cache result if enabled
                    if cache is not None and cache_key is not None:
                        cache.put(cache_key, result)
                    
                    self._stats['completed'] += 1
                    return
                    
                except asyncio.TimeoutError as e:
                    raise TimeoutError(f"Task {task_id} timed out after {queued_task.config.timeout}s") from e
                
            except Exception as e:
                last_error = e
                queued_task.retry_count = attempt
                logger.warning(f"Task {task_id} failed on attempt {attempt + 1}, error: {e}")
                
                if attempt == queued_task.config.retries:
                    # All retries exhausted
                    queued_task.error = e
                    queued_task.completed_at = time.time()
                    self._stats['failed'] += 1
        
        # If we get here, all retries failed
        if last_error:
            queued_task.error = last_error
            queued_task.completed_at = time.time()
            raise last_error
    
    @asynccontextmanager
    async def batch_processor(
        self,
        batch_size: int = 10,
        max_concurrent: int = 5
    ) -> AsyncGenerator[
        Callable[[Callable[..., Awaitable[Any]], tuple[Any, ...], dict[str, Any]], Awaitable[None]],
        None,
    ]:
        """Context manager for batch processing tasks."""
        batch_tasks: List[tuple[Callable[..., Awaitable[Any]], tuple[Any, ...], dict[str, Any]]] = []
        
        async def add_task(
            func: Callable[..., Awaitable[Any]],
            args: tuple[Any, ...],
            kwargs: dict[str, Any],
        ) -> None:
            batch_tasks.append((func, args, kwargs))
        
        try:
            yield add_task
            
            # Process batch when context exits
            if batch_tasks:
                # Submit all tasks
                task_ids = await self.submit_batch(
                    batch_tasks,
                    config=TaskConfig(max_concurrent=max_concurrent)
                )
                
                # Wait for all to complete
                results = []
                for task_id in task_ids:
                    result = await self.get_result(task_id)
                    results.append(result)
                
        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            raise


class BatchProcessor:
    """Utility class for batch processing with optimized performance."""
    
    def __init__(self, task_processor: AsyncTaskProcessor):
        self._task_processor = task_processor
    
    async def process_in_batches(
        self,
        items: List[T],
        processor_func: Callable[[T], Awaitable[R]],
        batch_size: int = 10,
        max_concurrent: int = 5,
        **kwargs: Any
    ) -> List[R]:
        """Process items in batches with concurrency control."""
        results = []
        
        # Split items into batches
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            # Create tasks for the batch
            batch_tasks = [
                (processor_func, (item,), kwargs)
                for item in batch
            ]
            
            # Submit and process batch
            task_ids = await self._task_processor.submit_batch(batch_tasks)
            
            # Collect results
            batch_results = []
            for task_id in task_ids:
                result = await self._task_processor.get_result(task_id)
                batch_results.append(result)
            
            results.extend(batch_results)
            
            # Brief pause between batches to prevent overwhelming
            await asyncio.sleep(0.01)
        
        return results


_async_task_processor_instance: AsyncTaskProcessor | None = None
_batch_processor_instance: BatchProcessor | None = None


def get_async_task_processor() -> AsyncTaskProcessor:
    """Get or create the global async task processor instance (lazy initialization)."""
    global _async_task_processor_instance
    if _async_task_processor_instance is None:
        _async_task_processor_instance = AsyncTaskProcessor(max_workers=20, queue_size=2000)
    return _async_task_processor_instance


def get_batch_processor() -> BatchProcessor:
    """Get or create the global batch processor instance (lazy initialization)."""
    global _batch_processor_instance
    if _batch_processor_instance is None:
        _batch_processor_instance = BatchProcessor(get_async_task_processor())
    return _batch_processor_instance


def async_task_processor_initialized() -> bool:
    """Check if the async task processor has been started."""
    global _async_task_processor_instance
    return _async_task_processor_instance is not None and bool(_async_task_processor_instance._worker_tasks)


async def initialize_async_task_processor() -> None:
    """Initialize and start the global async task processor.

    This should be called once during application startup, after the event loop is running.

    Raises:
        RuntimeError: If initialization fails.
    """
    processor = get_async_task_processor()
    try:
        await processor.start()
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize async task processor: {exc}") from exc


async def shutdown_async_task_processor() -> None:
    """Shutdown the global async task processor gracefully."""
    global _async_task_processor_instance, _batch_processor_instance
    if _async_task_processor_instance is not None:
        await _async_task_processor_instance.stop()
    _async_task_processor_instance = None
    _batch_processor_instance = None


def async_task(
    timeout: float = 30.0,
    retries: int = 3,
    priority: TaskPriority = TaskPriority.NORMAL,
    cache_result: bool = False,
    cache_ttl: float = 3600.0
) -> Callable[[Callable[..., Awaitable[R]]], Callable[..., Awaitable[R]]]:
    """Decorator to convert a function into an async task."""
    def decorator(func: Callable[..., Awaitable[R]]) -> Callable[..., Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            config = TaskConfig(
                timeout=timeout,
                retries=retries,
                priority=priority,
                cache_result=cache_result,
                cache_ttl=cache_ttl
            )

            processor = get_async_task_processor()
            task_id = await processor.submit(
                func, *args, config=config, **kwargs
            )

            return cast(R, await processor.get_result(task_id))

        return wrapper

    return decorator


def cpu_bound_task(
    timeout: float = 60.0,
    retries: int = 3,
    priority: TaskPriority = TaskPriority.NORMAL
) -> Callable[[Callable[..., R]], Callable[..., Awaitable[R]]]:
    """Decorator for CPU-bound tasks that should run in thread pool."""
    def decorator(func: Callable[..., R]) -> Callable[..., Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> R:
            config = TaskConfig(
                timeout=timeout,
                retries=retries,
                priority=priority
            )

            processor = get_async_task_processor()

            def run_in_thread() -> R:
                return func(*args, **kwargs)

            task_id = await processor.submit(
                lambda: asyncio.get_running_loop().run_in_executor(
                    processor._thread_pool._executor,
                    run_in_thread
                ),
                config=config
            )

            return cast(R, await processor.get_result(task_id))

        return wrapper

    return decorator


__all__ = [
    "AsyncTaskProcessor",
    "BatchProcessor",
    "TaskConfig",
    "TaskPriority",
    "async_task",
    "cpu_bound_task",
    "get_async_task_processor",
    "get_batch_processor",
    "initialize_async_task_processor",
    "shutdown_async_task_processor",
    "async_task_processor_initialized",
]
