-- QuizPDF cloud schema.
-- Run once in Supabase SQL Editor.
-- Existing local SQLite data is not modified.

begin;

create table if not exists public.profiles (
                                               id uuid primary key
                                               references auth.users(id) on delete cascade,
    display_name text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
    );

alter table public.profiles enable row level security;

-- These profile policies may already exist from the Auth phase.
drop policy if exists profiles_select_own on public.profiles;
drop policy if exists profiles_insert_own on public.profiles;
drop policy if exists profiles_update_own on public.profiles;

create policy profiles_select_own on public.profiles
    for select to authenticated
                                  using (id = (select auth.uid()));

create policy profiles_insert_own on public.profiles
    for insert to authenticated
    with check (id = (select auth.uid()));

create policy profiles_update_own on public.profiles
    for update to authenticated
                          using (id = (select auth.uid()))
        with check (id = (select auth.uid()));

create table if not exists public.exams (
                                            id text primary key,
                                            owner_id uuid not null
                                            references auth.users(id) on delete cascade,
    name text not null,
    subject text,
    year integer,
    author text,
    uploaded_by text,
    duration_minutes integer,
    source_file text,
    source_path text,
    source_sha256 text,
    uploaded_at timestamptz not null default now(),
    extra jsonb not null default '{}'::jsonb
    );

create table if not exists public.questions (
                                                id text primary key,
                                                composite_id text not null,
                                                exam_id text not null
                                                references public.exams(id) on delete cascade,
    question_id integer not null,
    type text not null check (
                                 type in (
                                 'mcp',
                                 'true_false_ng',
                                 'matching',
                                 'fill_in'
                                         )
    ),
    multiple_choice boolean not null default false,
    question_content jsonb not null default '[]'::jsonb,
    explanation_content jsonb not null default '[]'::jsonb,
    source_question_number integer,
    page_number integer,
    created_at timestamptz not null default now(),
    unique(exam_id, question_id),
    unique(exam_id, composite_id)
    );

create table if not exists public.mcp_options (
                                                  id text primary key,
                                                  question_id text not null
                                                  references public.questions(id) on delete cascade,
    label text not null,
    content jsonb not null default '[]'::jsonb,
    is_correct boolean not null default false,
    order_index integer not null default 0,
    unique(question_id, label)
    );

create table if not exists public.match_pairs (
                                                  id text primary key,
                                                  question_id text not null
                                                  references public.questions(id) on delete cascade,
    description_content jsonb,
    answer_content jsonb not null default '[]'::jsonb,
    is_distractor boolean not null default false,
    order_index integer not null default 0
    );

create table if not exists public.fill_in_answers (
                                                      id text primary key,
                                                      question_id text not null
                                                      references public.questions(id) on delete cascade,
    content jsonb not null default '[]'::jsonb,
    order_index integer not null default 0
    );

create table if not exists public.question_assets (
                                                      question_id text primary key
                                                      references public.questions(id) on delete cascade,
    image_path text,
    page_number integer,
    crop_coordinates jsonb,
    crop_status text not null default 'not_created',
    source text,
    updated_at timestamptz not null default now()
    );

create table if not exists public.red_mark_detections (
                                                          question_id text primary key
                                                          references public.questions(id) on delete cascade,
    detected_answer text,
    confidence double precision not null default 0,
    status text not null,
    page_number integer,
    mark_coordinates jsonb,
    user_confirmed_answer text,
    config_fingerprint text not null,
    updated_at timestamptz not null default now()
    );

create table if not exists public.quiz_attempts (
                                                    id text primary key,
                                                    exam_id text not null
                                                    references public.exams(id) on delete cascade,
    owner_id uuid not null
    references auth.users(id) on delete cascade,
    score integer not null check (score between 0 and 100),
    total_questions integer not null check (total_questions >= 0),
    correct_answers_count integer not null
    check (correct_answers_count >= 0),
    time_spent integer not null check (time_spent >= 0),
    answers jsonb not null default '{}'::jsonb,
    question_results jsonb not null default '[]'::jsonb,
    completed_at bigint not null
    );

create table if not exists public.upload_jobs (
                                                  id uuid primary key,
                                                  owner_id uuid not null
                                                  references auth.users(id) on delete cascade,
    status text not null check (
                                   status in ('queued', 'processing', 'completed', 'failed')
    ),
    phase text not null default 'queued',
    result jsonb,
    error text,
    error_status integer,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
    );

create index if not exists idx_cloud_exams_owner
    on public.exams(owner_id, uploaded_at desc);

create index if not exists idx_cloud_questions_exam
    on public.questions(exam_id, question_id);

create index if not exists idx_cloud_options_question
    on public.mcp_options(question_id, order_index);

create index if not exists idx_cloud_match_question
    on public.match_pairs(question_id, order_index);

create index if not exists idx_cloud_fill_question
    on public.fill_in_answers(question_id, order_index);

create index if not exists idx_cloud_attempts_owner
    on public.quiz_attempts(owner_id, completed_at desc);

create index if not exists idx_cloud_attempts_exam
    on public.quiz_attempts(exam_id);

create index if not exists idx_cloud_jobs_owner
    on public.upload_jobs(owner_id, created_at desc);

-- Ownership of exam records.

alter table public.exams enable row level security;
alter table public.questions enable row level security;
alter table public.mcp_options enable row level security;
alter table public.match_pairs enable row level security;
alter table public.fill_in_answers enable row level security;
alter table public.question_assets enable row level security;
alter table public.red_mark_detections enable row level security;
alter table public.quiz_attempts enable row level security;
alter table public.upload_jobs enable row level security;

create policy quizpdf_exams_owner on public.exams
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

create policy quizpdf_questions_owner on public.questions
    for all to authenticated
    using (
        exists (
            select 1 from public.exams e
            where e.id = exam_id
              and e.owner_id = (select auth.uid())
        )
    )
    with check (
        exists (
            select 1 from public.exams e
            where e.id = exam_id
              and e.owner_id = (select auth.uid())
        )
    );

-- Called by child-table policies.
-- SECURITY INVOKER preserves the caller's RLS restrictions.

create function public.quizpdf_owns_question(
    p_question_id text
)
    returns boolean
    language sql
    stable
security invoker
set search_path = ''
as $$
select exists (
    select 1
    from public.questions q
             join public.exams e on e.id = q.exam_id
    where q.id = p_question_id
      and e.owner_id = (select auth.uid())
);
$$;

create policy quizpdf_options_owner on public.mcp_options
    for all to authenticated
    using (public.quizpdf_owns_question(question_id))
    with check (public.quizpdf_owns_question(question_id));

create policy quizpdf_match_owner on public.match_pairs
    for all to authenticated
    using (public.quizpdf_owns_question(question_id))
    with check (public.quizpdf_owns_question(question_id));

create policy quizpdf_fill_owner on public.fill_in_answers
    for all to authenticated
    using (public.quizpdf_owns_question(question_id))
    with check (public.quizpdf_owns_question(question_id));

create policy quizpdf_assets_owner on public.question_assets
    for all to authenticated
    using (public.quizpdf_owns_question(question_id))
    with check (public.quizpdf_owns_question(question_id));

create policy quizpdf_red_marks_owner
    on public.red_mark_detections
    for all to authenticated
    using (public.quizpdf_owns_question(question_id))
    with check (public.quizpdf_owns_question(question_id));

create policy quizpdf_attempts_owner on public.quiz_attempts
    for all to authenticated
    using (
        owner_id = (select auth.uid())
        and exists (
            select 1 from public.exams e
            where e.id = exam_id
              and e.owner_id = (select auth.uid())
        )
    )
    with check (
        owner_id = (select auth.uid())
        and exists (
            select 1 from public.exams e
            where e.id = exam_id
              and e.owner_id = (select auth.uid())
        )
    );

create policy quizpdf_upload_jobs_owner on public.upload_jobs
    for all to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

-- Atomically persist one OCR result and all its question children.

create function public.quizpdf_create_exam_bundle(
    p_exam jsonb,
    p_questions jsonb
)
    returns jsonb
    language plpgsql
security invoker
set search_path = ''
as $$
declare
v_owner uuid := auth.uid();
    v_exam_id text := p_exam->>'id';
    v_question jsonb;
    v_option jsonb;
    v_pair jsonb;
    v_answer jsonb;
    v_asset jsonb;
    v_count integer := 0;
begin
    if v_owner is null then
        raise exception 'Authentication required'
            using errcode = '28000';
end if;

    if v_exam_id is null
       or jsonb_typeof(p_questions) is distinct from 'array'
    then
        raise exception 'Invalid exam bundle'
            using errcode = '22023';
end if;

insert into public.exams (
    id, owner_id, name, subject, year, author,
    uploaded_by, duration_minutes, source_file,
    source_path, source_sha256, uploaded_at, extra
)
values (
           v_exam_id,
           v_owner,
           p_exam->>'name',
           p_exam->>'subject',
           (p_exam->>'year')::integer,
           p_exam->>'author',
           p_exam->>'uploaded_by',
           (p_exam->>'duration_minutes')::integer,
           p_exam->>'source_file',
           p_exam->>'source_path',
           p_exam->>'source_sha256',
           coalesce(
                   (p_exam->>'uploaded_at')::timestamptz,
                   now()
           ),
           coalesce(p_exam->'extra', '{}'::jsonb)
       );

for v_question in
select value from jsonb_array_elements(p_questions)
                      loop
    insert into public.questions (
    id, composite_id, exam_id, question_id, type,
    multiple_choice, question_content,
    explanation_content, source_question_number,
    page_number, created_at
)
values (
    v_question->>'id',
    v_question->>'composite_id',
    v_exam_id,
    (v_question->>'question_id')::integer,
    v_question->>'type',
    coalesce(
    (v_question->>'multiple_choice')::boolean,
    false
    ),
    coalesce(
    v_question->'question_content',
    '[]'::jsonb
    ),
    coalesce(
    v_question->'explanation_content',
    '[]'::jsonb
    ),
    coalesce(
    (v_question->>'source_question_number')::integer,
    (v_question->>'question_id')::integer
    ),
    (v_question->>'page_number')::integer,
    coalesce(
    (v_question->>'created_at')::timestamptz,
    now()
    )
    );

for v_option in
select value from jsonb_array_elements(
                          coalesce(
                                  v_question->'mcp_options',
                                  '[]'::jsonb
                          )
                  )
                      loop
    insert into public.mcp_options (
    id, question_id, label, content,
    is_correct, order_index
)
values (
    v_option->>'id',
    v_question->>'id',
    v_option->>'label',
    coalesce(v_option->'content', '[]'::jsonb),
    coalesce(
    (v_option->>'is_correct')::boolean,
    false
    ),
    coalesce(
    (v_option->>'order_index')::integer,
    0
    )
    );
end loop;

for v_pair in
select value from jsonb_array_elements(
                          coalesce(
                                  v_question->'match_pairs',
                                  '[]'::jsonb
                          )
                  )
                      loop
    insert into public.match_pairs (
    id, question_id, description_content,
    answer_content, is_distractor, order_index
)
values (
    v_pair->>'id',
    v_question->>'id',
    v_pair->'description_content',
    coalesce(
    v_pair->'answer_content',
    '[]'::jsonb
    ),
    coalesce(
    (v_pair->>'is_distractor')::boolean,
    false
    ),
    coalesce(
    (v_pair->>'order_index')::integer,
    0
    )
    );
end loop;

for v_answer in
select value from jsonb_array_elements(
                          coalesce(
                                  v_question->'fill_in_answers',
                                  '[]'::jsonb
                          )
                  )
                      loop
    insert into public.fill_in_answers (
    id, question_id, content, order_index
)
values (
    v_answer->>'id',
    v_question->>'id',
    coalesce(
    v_answer->'content',
    '[]'::jsonb
    ),
    coalesce(
    (v_answer->>'order_index')::integer,
    0
    )
    );
end loop;

        v_asset := v_question->'asset';

        if v_asset is not null
           and v_asset <> 'null'::jsonb
        then
            insert into public.question_assets (
                question_id, image_path, page_number,
                crop_coordinates, crop_status, source
            )
            values (
                v_question->>'id',
                v_asset->>'image_path',
                (v_asset->>'page_number')::integer,
                v_asset->'crop_coordinates',
                coalesce(
                    v_asset->>'crop_status',
                    'not_created'
                ),
                v_asset->>'source'
            );
end if;

        v_count := v_count + 1;
end loop;

return jsonb_build_object(
        'exam_id', v_exam_id,
        'question_count', v_count
       );
end;
$$;

-- Access via Supabase Data API.

revoke all on table
    public.profiles,
    public.exams,
    public.questions,
    public.mcp_options,
    public.match_pairs,
    public.fill_in_answers,
    public.question_assets,
    public.red_mark_detections,
    public.quiz_attempts,
    public.upload_jobs
    from anon;

grant select, insert, update on public.profiles
    to authenticated;

grant select, insert, update, delete on
    public.exams,
    public.questions,
    public.mcp_options,
    public.match_pairs,
    public.fill_in_answers,
    public.question_assets,
    public.red_mark_detections,
    public.quiz_attempts,
    public.upload_jobs
    to authenticated;

revoke all on function
    public.quizpdf_owns_question(text)
    from public;

revoke all on function
    public.quizpdf_create_exam_bundle(jsonb, jsonb)
    from public;

grant execute on function
public.quizpdf_owns_question(text)
to authenticated;

grant execute on function
    public.quizpdf_create_exam_bundle(jsonb, jsonb)
to authenticated;

-- Private Storage for PDFs and cropped question images.

insert into storage.buckets (
    id, name, public, file_size_limit, allowed_mime_types
)
values (
           'quizpdf-private',
           'quizpdf-private',
           false,
           31457280,
           array[
               'application/pdf',
           'image/png',
           'image/jpeg'
               ]
       )
    on conflict (id) do update
                            set public = false;

create policy quizpdf_storage_read on storage.objects
    for select to authenticated
                   using (
                   bucket_id = 'quizpdf-private'
                   and (storage.foldername(name))[1]
                   = (select auth.uid())::text
                   );

create policy quizpdf_storage_insert on storage.objects
    for insert to authenticated
    with check (
        bucket_id = 'quizpdf-private'
        and (storage.foldername(name))[1]
            = (select auth.uid())::text
    );

create policy quizpdf_storage_update on storage.objects
    for update to authenticated
                          using (
                          bucket_id = 'quizpdf-private'
                          and (storage.foldername(name))[1]
                          = (select auth.uid())::text
                          )
        with check (
                          bucket_id = 'quizpdf-private'
                          and (storage.foldername(name))[1]
                          = (select auth.uid())::text
                          );

create policy quizpdf_storage_delete on storage.objects
    for delete to authenticated
    using (
        bucket_id = 'quizpdf-private'
        and (storage.foldername(name))[1]
            = (select auth.uid())::text
    );

commit;
