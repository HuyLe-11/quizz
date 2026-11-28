-- QuizPDF syllabus RAG schema.
-- Review against the target Supabase project's installed extensions before applying.
-- This migration is intentionally separate from the existing cloud schema.

begin;

-- The existing repository migration does not enable pgvector. Create it only
-- when absent; fail clearly if this project has it installed in another schema.
create schema if not exists extensions;

do $$
declare
    vector_schema text;
begin
    select ns.nspname
      into vector_schema
      from pg_catalog.pg_extension as ext
      join pg_catalog.pg_namespace as ns
        on ns.oid = ext.extnamespace
     where ext.extname = 'vector';

    if vector_schema is null then
        execute 'create extension vector with schema extensions';
    elsif vector_schema <> 'extensions' then
        raise exception
            'pgvector is installed in schema "%"; review this migration and update vector type/operator references before applying',
            vector_schema;
    end if;
end;
$$;

create table public.syllabus_documents (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null
        references auth.users(id) on delete cascade,
    title text not null check (length(btrim(title)) > 0),
    subject text,
    source_file text not null check (length(btrim(source_file)) > 0),
    source_path text not null check (length(btrim(source_path)) > 0),
    processing_status text not null default 'queued'
        check (processing_status in ('queued', 'processing', 'completed', 'failed')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
        check (jsonb_typeof(metadata) = 'object'),
    unique (owner_id, source_path),
    -- Required for the chunk composite FK that enforces matching ownership.
    unique (id, owner_id)
);

create table public.syllabus_chunks (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null,
    owner_id uuid not null
        references auth.users(id) on delete cascade,
    chunk_index integer not null check (chunk_index >= 0),
    content text not null check (length(btrim(content)) > 0),
    source_pages integer[] not null
        check (
            cardinality(source_pages) > 0
            and 0 < all (source_pages)
            and array_position(source_pages, null) is null
        ),
    section_heading text,
    character_count integer not null
        check (character_count = char_length(content)),
    token_estimate integer check (token_estimate is null or token_estimate >= 0),
    embedding extensions.vector(1536),
    created_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb
        check (jsonb_typeof(metadata) = 'object'),
    constraint syllabus_chunks_document_owner_fkey
        foreign key (document_id, owner_id)
        references public.syllabus_documents (id, owner_id)
        on delete cascade,
    unique (document_id, chunk_index)
);

create index idx_syllabus_documents_owner_created
    on public.syllabus_documents (owner_id, created_at desc);

create index idx_syllabus_documents_owner_status_updated
    on public.syllabus_documents (owner_id, processing_status, updated_at desc);

create index idx_syllabus_chunks_owner_document_order
    on public.syllabus_chunks (owner_id, document_id, chunk_index);

-- 1536 dimensions fit the standard vector HNSW index limit. The cosine opclass
-- matches the retrieval RPC's <=> distance operator.
create index idx_syllabus_chunks_embedding_hnsw
    on public.syllabus_chunks
    using hnsw (embedding extensions.vector_cosine_ops)
    where embedding is not null;

alter table public.syllabus_documents enable row level security;
alter table public.syllabus_chunks enable row level security;

create policy quizpdf_syllabus_documents_owner
    on public.syllabus_documents
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

create policy quizpdf_syllabus_chunks_owner
    on public.syllabus_chunks
    for all to authenticated
    using (
        owner_id = (select auth.uid())
        and exists (
            select 1
              from public.syllabus_documents as document
             where document.id = document_id
               and document.owner_id = (select auth.uid())
        )
    )
    with check (
        owner_id = (select auth.uid())
        and exists (
            select 1
              from public.syllabus_documents as document
             where document.id = document_id
               and document.owner_id = (select auth.uid())
        )
    );

-- Persist one complete syllabus bundle under the caller's JWT. Explicit
-- document IDs and conflict verification make retries safe after a lost
-- response, while the function remains subject to table grants and RLS.
create or replace function public.quizpdf_create_syllabus_bundle(
    p_document jsonb,
    p_chunks jsonb
)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_owner uuid := auth.uid();
    v_document_id uuid;
    v_chunk jsonb;
    v_page jsonb;
    v_index integer := 0;
    v_payload_index integer;
    v_page_number integer;
    v_character_count integer;
    v_token_estimate integer;
    v_pages integer[];
    v_embedding extensions.vector(1536);
    v_processing_status text;
    v_existing public.syllabus_documents%rowtype;
    v_existing_chunk public.syllabus_chunks%rowtype;
    v_document_inserted integer;
    v_expected_count integer;
    v_actual_count integer;
begin
    if v_owner is null then
        raise exception 'Authentication required' using errcode = '28000';
    end if;
    if jsonb_typeof(p_document) is distinct from 'object' then
        raise exception 'p_document must be a JSON object' using errcode = '22023';
    end if;
    if jsonb_typeof(p_chunks) is distinct from 'array' then
        raise exception 'p_chunks must be a JSON array' using errcode = '22023';
    end if;
    v_expected_count := jsonb_array_length(p_chunks);
    if v_expected_count = 0 then
        raise exception 'p_chunks must contain at least one chunk' using errcode = '22023';
    end if;

    if jsonb_typeof(p_document->'id') is distinct from 'string' then
        raise exception 'Syllabus document id must be a UUID string' using errcode = '22023';
    end if;
    begin
        v_document_id := (p_document->>'id')::uuid;
    exception when others then
        raise exception 'Syllabus document id must be a UUID' using errcode = '22023';
    end;

    if jsonb_typeof(p_document->'title') is distinct from 'string'
       or jsonb_typeof(p_document->'source_file') is distinct from 'string'
       or jsonb_typeof(p_document->'source_path') is distinct from 'string'
       or nullif(btrim(p_document->>'title'), '') is null
       or nullif(btrim(p_document->>'source_file'), '') is null
       or nullif(btrim(p_document->>'source_path'), '') is null then
        raise exception 'Invalid syllabus document payload or Storage path' using errcode = '22023';
    end if;
    if p_document ? 'processing_status'
       and jsonb_typeof(p_document->'processing_status') is distinct from 'string' then
        raise exception 'processing_status must be a string' using errcode = '22023';
    end if;
    v_processing_status := coalesce(p_document->>'processing_status', 'completed');
    if v_processing_status not in ('queued', 'processing', 'completed', 'failed') then
        raise exception 'Invalid syllabus processing_status' using errcode = '22023';
    end if;
    if p_document ? 'subject'
       and jsonb_typeof(p_document->'subject') not in ('string', 'null') then
        raise exception 'subject must be a string or null' using errcode = '22023';
    end if;
    if p_document ? 'metadata'
       and jsonb_typeof(p_document->'metadata') is distinct from 'object' then
        raise exception 'document metadata must be a JSON object' using errcode = '22023';
    end if;
    if left(
        p_document->>'source_path',
        length(v_owner::text || '/syllabi/' || v_document_id::text || '/')
    ) <> (v_owner::text || '/syllabi/' || v_document_id::text || '/')
       or length(p_document->>'source_path') <=
          length(v_owner::text || '/syllabi/' || v_document_id::text || '/') then
        raise exception 'Storage path must be scoped to the authenticated owner and document' using errcode = '22023';
    end if;

    insert into public.syllabus_documents (
        id, owner_id, title, subject, source_file, source_path,
        processing_status, metadata
    ) values (
        v_document_id, v_owner, btrim(p_document->>'title'),
        p_document->>'subject', btrim(p_document->>'source_file'),
        p_document->>'source_path',
        v_processing_status,
        coalesce(p_document->'metadata', '{}'::jsonb)
    ) on conflict (id) do nothing;
    get diagnostics v_document_inserted = row_count;

    select * into v_existing from public.syllabus_documents where id = v_document_id;
    if not found
       or v_existing.owner_id <> v_owner
       or v_existing.title <> btrim(p_document->>'title')
       or v_existing.subject is distinct from p_document->>'subject'
       or v_existing.source_file <> btrim(p_document->>'source_file')
       or v_existing.source_path <> p_document->>'source_path'
       or v_existing.processing_status <> v_processing_status
       or v_existing.metadata is distinct from coalesce(p_document->'metadata', '{}'::jsonb) then
        raise exception 'Syllabus retry conflicts with stored document' using errcode = '23505';
    end if;

    for v_chunk in
        select elem.value
          from jsonb_array_elements(p_chunks)
               with ordinality as elem(value, ordinality)
         order by elem.ordinality
    loop
        if jsonb_typeof(v_chunk) is distinct from 'object' then
            raise exception 'Invalid syllabus chunk at index %', v_index using errcode = '22023';
        end if;
        if jsonb_typeof(v_chunk->'chunk_index') is distinct from 'number'
           or (v_chunk->>'chunk_index') !~ '^[0-9]+$' then
            raise exception 'chunk_index must be a non-negative integer at index %', v_index using errcode = '22023';
        end if;
        begin
            v_payload_index := (v_chunk->>'chunk_index')::integer;
        exception when others then
            raise exception 'chunk_index is out of range at index %', v_index using errcode = '22023';
        end;
        if v_payload_index <> v_index then
            raise exception 'chunk_index values must be contiguous from zero' using errcode = '22023';
        end if;
        if jsonb_typeof(v_chunk->'content') is distinct from 'string'
           or nullif(btrim(v_chunk->>'content'), '') is null then
            raise exception 'chunk content must be non-empty text at index %', v_index using errcode = '22023';
        end if;
        if jsonb_typeof(v_chunk->'source_pages') is distinct from 'array' then
            raise exception 'source_pages must be an array at chunk index %', v_index using errcode = '22023';
        end if;
        if jsonb_array_length(v_chunk->'source_pages') = 0 then
            raise exception 'source_pages must not be empty at chunk index %', v_index using errcode = '22023';
        end if;
        if jsonb_typeof(v_chunk->'embedding') is distinct from 'array' then
            raise exception 'embedding must be an array at chunk index %', v_index using errcode = '22023';
        end if;
        if jsonb_array_length(v_chunk->'embedding') <> 1536 then
            raise exception 'embedding must have 1536 dimensions at chunk index %', v_index using errcode = '22023';
        end if;
        if v_chunk ? 'metadata'
           and jsonb_typeof(v_chunk->'metadata') is distinct from 'object' then
            raise exception 'chunk metadata must be a JSON object at index %', v_index using errcode = '22023';
        end if;
        if v_chunk ? 'section_heading'
           and jsonb_typeof(v_chunk->'section_heading') not in ('string', 'null') then
            raise exception 'section_heading must be a string or null at index %', v_index using errcode = '22023';
        end if;
        if jsonb_typeof(v_chunk->'character_count') is distinct from 'number'
           or (v_chunk->>'character_count') !~ '^[0-9]+$' then
            raise exception 'character_count must be a non-negative integer at index %', v_index using errcode = '22023';
        end if;
        begin
            v_character_count := (v_chunk->>'character_count')::integer;
        exception when others then
            raise exception 'character_count is out of range at index %', v_index using errcode = '22023';
        end;
        if v_character_count <> char_length(v_chunk->>'content') then
            raise exception 'character_count does not match content at index %', v_index using errcode = '22023';
        end if;
        if v_chunk ? 'token_estimate' and jsonb_typeof(v_chunk->'token_estimate') <> 'null' then
            if jsonb_typeof(v_chunk->'token_estimate') is distinct from 'number'
               or (v_chunk->>'token_estimate') !~ '^[0-9]+$' then
                raise exception 'token_estimate must be a non-negative integer or null at index %', v_index using errcode = '22023';
            end if;
            begin
                v_token_estimate := (v_chunk->>'token_estimate')::integer;
            exception when others then
                raise exception 'token_estimate is out of range at index %', v_index using errcode = '22023';
            end;
        else
            v_token_estimate := null;
        end if;

        v_pages := array[]::integer[];
        for v_page in
            select elem.value
              from jsonb_array_elements(v_chunk->'source_pages')
                   with ordinality as elem(value, ordinality)
             order by elem.ordinality
        loop
            if jsonb_typeof(v_page) is distinct from 'number'
               or (v_page #>> '{}') !~ '^[0-9]+$' then
                raise exception 'source pages must be positive integers at chunk index %', v_index using errcode = '22023';
            end if;
            begin
                v_page_number := (v_page #>> '{}')::integer;
            exception when others then
                raise exception 'source page number is out of range at chunk index %', v_index using errcode = '22023';
            end;
            if v_page_number < 1 then
                raise exception 'source pages must be positive integers at chunk index %', v_index using errcode = '22023';
            end if;
            v_pages := array_append(v_pages, v_page_number);
        end loop;

        if exists (
            select 1 from jsonb_array_elements(v_chunk->'embedding') as item(value)
            where jsonb_typeof(item.value) is distinct from 'number'
        ) then
            raise exception 'Embedding values must be JSON numbers at chunk index %', v_index using errcode = '22023';
        end if;
        begin
            v_embedding := (v_chunk->'embedding')::text::extensions.vector(1536);
        exception when others then
            raise exception 'Invalid 1536-dimensional embedding at chunk index %', v_index using errcode = '22023';
        end;
        if extensions.vector_norm(v_embedding) = 0 then
            raise exception 'Embedding must have non-zero norm at chunk index %', v_index using errcode = '22023';
        end if;

        insert into public.syllabus_chunks (
            document_id, owner_id, chunk_index, content, source_pages,
            section_heading, character_count, token_estimate, embedding, metadata
        ) values (
            v_document_id, v_owner, v_index, v_chunk->>'content', v_pages,
            v_chunk->>'section_heading', v_character_count,
            v_token_estimate, v_embedding,
            coalesce(v_chunk->'metadata', '{}'::jsonb)
        ) on conflict (document_id, chunk_index) do nothing;

        select * into v_existing_chunk
          from public.syllabus_chunks
         where document_id = v_document_id and chunk_index = v_index;
        if not found
           or v_existing_chunk.owner_id <> v_owner
           or v_existing_chunk.content <> v_chunk->>'content'
           or v_existing_chunk.source_pages <> v_pages
           or v_existing_chunk.section_heading is distinct from v_chunk->>'section_heading'
           or v_existing_chunk.character_count <> v_character_count
           or v_existing_chunk.token_estimate is distinct from v_token_estimate
           or v_existing_chunk.embedding is distinct from v_embedding
           or v_existing_chunk.metadata is distinct from coalesce(v_chunk->'metadata', '{}'::jsonb) then
            raise exception 'Syllabus retry conflicts with stored chunk %', v_index using errcode = '23505';
        end if;
        v_index := v_index + 1;
    end loop;

    select count(*) into v_actual_count
      from public.syllabus_chunks
     where document_id = v_document_id and owner_id = v_owner;
    if v_actual_count <> v_expected_count then
        raise exception 'Syllabus retry has a different chunk count' using errcode = '23505';
    end if;
    return jsonb_build_object(
        'document_id', v_document_id,
        'chunk_count', v_expected_count,
        'already_persisted', v_document_inserted = 0
    );
end;
$$;

revoke all on function public.quizpdf_create_syllabus_bundle(jsonb, jsonb) from public;

-- Keep updated_at consistent for both document metadata and processing changes.
create or replace function public.quizpdf_syllabus_set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at := pg_catalog.now();
    return new;
end;
$$;

create trigger syllabus_documents_set_updated_at
    before update on public.syllabus_documents
    for each row execute function public.quizpdf_syllabus_set_updated_at();

revoke all on function public.quizpdf_syllabus_set_updated_at() from public;

-- SECURITY INVOKER ensures table grants and both owner RLS policies apply.
-- Filters are part of the function so they are applied before the result limit.
create or replace function public.quizpdf_match_syllabus_chunks(
    query_embedding extensions.vector(1536),
    p_match_count integer default 10,
    p_subject text default null,
    p_document_id uuid default null
)
returns table (
    chunk_id uuid,
    document_id uuid,
    document_title text,
    subject text,
    chunk_index integer,
    content text,
    source_pages integer[],
    section_heading text,
    similarity double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        chunk.id,
        chunk.document_id,
        document.title,
        document.subject,
        chunk.chunk_index,
        chunk.content,
        chunk.source_pages,
        chunk.section_heading,
        (1 - (chunk.embedding OPERATOR(extensions.<=>) query_embedding))::double precision
    from public.syllabus_chunks as chunk
    join public.syllabus_documents as document
      on document.id = chunk.document_id
     and document.owner_id = chunk.owner_id
    where (select auth.uid()) is not null
      and chunk.owner_id = (select auth.uid())
      and chunk.embedding is not null
      and (p_subject is null or document.subject = p_subject)
      and (p_document_id is null or chunk.document_id = p_document_id)
    order by chunk.embedding OPERATOR(extensions.<=>) query_embedding
    limit least(greatest(coalesce(p_match_count, 10), 1), 100);
$$;

revoke all on function public.quizpdf_match_syllabus_chunks(
    extensions.vector, integer, text, uuid
) from public;

revoke all on table public.syllabus_documents, public.syllabus_chunks
    from public, anon;

grant select, insert, update, delete
    on public.syllabus_documents, public.syllabus_chunks
    to authenticated;

grant execute on function public.quizpdf_match_syllabus_chunks(
    extensions.vector, integer, text, uuid
) to authenticated;

grant execute on function public.quizpdf_create_syllabus_bundle(jsonb, jsonb)
    to authenticated;

commit;
