-- Correct pgvector equality resolution for an empty SECURITY INVOKER search_path.
-- Replaces only the atomic syllabus bundle RPC; tables, RLS and Storage are unchanged.
begin;
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
           or (
               (v_existing_chunk.embedding is null and v_embedding is not null)
               or (v_existing_chunk.embedding is not null and v_embedding is null)
               or (
                   v_existing_chunk.embedding is not null
                   and v_embedding is not null
                   and not (
                       v_existing_chunk.embedding OPERATOR(extensions.=) v_embedding
                   )
               )
           )
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
grant execute on function public.quizpdf_create_syllabus_bundle(jsonb, jsonb) to authenticated;

commit;
