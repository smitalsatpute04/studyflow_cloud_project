# CS-402: Distributed Cloud Systems & AI Architecture

## Module 1: Cloud-Native Compute & Container Orchestration
Modern cloud applications decouple stateless web services from stateful backing stores.
Containers package application code, runtime dependencies, and system libraries into immutable artifacts.
Horizontal Pod Autoscaling adjusts replica counts dynamically based on CPU, memory, or custom queue depth metrics.

## Module 2: Distributed Storage & Object Stores (S3 / Cloudflare R2)
Object storage systems manage unstructured blobs (PDFs, media, backups) using flat namespaces and HTTP REST APIs.
Unlike POSIX block filesystems, S3-compatible stores provide high durability, eventual or strong read-after-write consistency, and presigned URLs for zero-trust client access.
Cloudflare R2 eliminates egress bandwidth fees while maintaining AWS S3 API compatibility.

## Module 3: Relational Persistence & Connection Pooling (PostgreSQL)
ACID transactions guarantee Atomicity, Consistency, Isolation, and Durability.
Multi-Version Concurrency Control (MVCC) allows readers and writers to operate without blocking each other.
Connection poolers (such as SQLAlchemy QueuePool or PgBouncer) prevent connection exhaustion under high concurrency.

## Module 4: Asynchronous Task Queues (Redis & RQ / Celery)
Long-running operations—such as parsing multi-megabyte PDF textbooks or running multi-step LLM reasoning pipelines—should be offloaded from synchronous HTTP request threads.
Producer-consumer queues backed by Redis persist job payloads in memory and distribute work across background worker processes with retry policies and timeouts.

## Module 5: Structured LLM Pipelines (Google Gemini API)
Production AI applications enforce deterministic output schemas using Pydantic models and `response_mime_type="application/json"`.
Controlled temperature settings, context truncation, and rubric-grounded evaluation reduce hallucination during automated assessment generation and grading.
