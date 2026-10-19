---
id: R11
version: "2"
severidade: alta
fundamento: "Nos 120 dias anteriores à data das eleições gerais e municipais, os deputados que forem candidatos não poderão utilizar recursos da cota para pagar divulgação da atividade parlamentar (Ato da Mesa 40/2012)."
---

# R11 — Divulgação durante o período eleitoral vedado

## Fundamento e fontes

A [fonte da regra](../../cota-parlamentar.md) estabelece a restrição de
divulgação da atividade parlamentar para deputados candidatos durante os
120 dias anteriores à eleição. A categoria de despesa é `5`, correspondente
a `DIVULGAÇÃO DA ATIVIDADE PARLAMENTAR`.

A despesa vem de `despesas_current`. A condição de candidato depende do
último conjunto TSE importado com sucesso para cada ano, preservando o snapshot
e a origem do registro. Os dados TSE são obtidos por
[`fetch tse`](../../docs/runbooks/fetch.md); a regra não consulta a rede.

As fontes primárias de candidatos são os catálogos oficiais do TSE para
[2024](https://dadosabertos.tse.jus.br/dataset/candidatos-2024) e
[2026](https://dadosabertos.tse.jus.br/dataset/candidatos-2026). A seleção de
data, turno e tipo segue os campos dos arquivos oficiais dessas fontes.

## Campos e eleições abrangidas

Usar `id_documento`, `numero_deputado_id`, `nome_parlamentar`, `cpf`,
`sigla_uf`, `numero_sub_cota`, `data_emissao` e `valor_liquido`. Cruzar com as
identidades de candidatos da importação corrente do mesmo ano eleitoral.

Esta versão contempla as eleições ordinárias de primeiro turno:

- Municipal de 2024: eleição em `2024-10-06`, janela iniciada em `2024-06-08`.
- Geral de 2026: eleição em `2026-10-04`, janela iniciada em `2026-06-06`.

A importação exige data da eleição, turno e tipo. Seleciona apenas a data
prevista para o ano, turno `1` e tipo `2` (`ELEIÇÃO ORDINÁRIA`), excluindo
eleições suplementares e outros turnos. A versão não estende essas datas
automaticamente a outras eleições.

## Correspondência do candidato

Priorizar a igualdade de CPF integral normalizado. Na ausência de CPF em pelo
menos um dos lados, admitir correspondência pelo nome normalizado completo e
pela mesma UF. CPFs integrais diferentes nunca podem ser contornados por uma
coincidência de nome.

A correspondência por CPF tem prioridade sobre alternativas por nome em todo
o ano eleitoral. Várias candidaturas com o mesmo CPF integral representam uma
identidade para esse controle, e todos os respectivos registros são conservados
na evidência. CPF mascarado ou indisponível não é reconstruído pelo sistema.

A normalização remove acentos, uniformiza maiúsculas e separadores, e compara
o nome inteiro; não usa sobrenome isolado nem busca aproximada. Identidades
distintas de candidatos que tornem a correspondência por nome ambígua causam
achado de severidade `revisar`, preservando todos os candidatos. Não selecionar arbitrariamente um homônimo.

## Lógica determinística

Selecionar despesas da categoria `5` com candidato correspondente e emissão
no intervalo `data_eleicao - 120 dias <= data_emissao < data_eleicao`. O
primeiro dia da janela está incluído; o dia da eleição está excluído. Emissão
ausente não permite estabelecer o enquadramento temporal.

Emitir um achado para cada documento que satisfaça as condições. O valor do
achado é o `valor_liquido` do documento, em centavos exatos. A proveniência
preserva arquivo, registro, execução, etapa e versão, além da origem TSE usada
na correspondência.

A ausência de uma relação exigida segue o fluxo de regra ignorada com motivo
registrado. Quando as relações existem, mas não há importação bem-sucedida para
o ano de uma despesa relevante dentro da janela, a regra falha. Um conjunto
importado com sucesso e vazio é válido e não produz correspondências. Falhas
de correspondência ambígua não publicam resultados parciais. Inspecione o ano
e o resultado da importação ao interpretar esses desfechos.

## Evidência

Os campos temporais são `ano_eleicao`, `data_eleicao`,
`inicio_periodo_vedado`, `fim_periodo_exclusivo` e `data_emissao`, com datas
serializadas no formato ISO. A categoria consta em `numero_sub_cota`.

Registrar `metodo_correspondencia` como `cpf` ou `nome_uf`, junto de
`nome_parlamentar_normalizado`, `cpf_parlamentar` e `uf_parlamentar`. A origem
externa é identificada por `snapshot_id` e `snapshot_sha256`.

Cada elemento de `candidatos` contém `candidate_id`, `cpf`,
`nome_normalizado`, `uf`, `cargo`, `source_member` e `source_record_number`.
Esses dados permitem reproduzir a escolha sem depender de uma consulta futura
à fonte externa. O valor líquido fica no valor do achado e no registro de
despesa associado.

## Exemplos de fronteira e correspondência

Para um parlamentar com candidato confirmado na eleição ordinária de 2024:

- Categoria `5`, emissão em `2024-06-08`: está no limite inicial e dispara.
- Emissão em `2024-06-07`: está fora da janela e não dispara.
- Emissão em `2024-10-05`: está na janela e dispara.
- Emissão em `2024-10-06`: é o dia da eleição e não dispara.
- Outra categoria, ainda que dentro da janela: não dispara esta regra.

CPF integral coincidente permite o cruzamento principal. Sem CPF em um dos
lados, nome normalizado completo e UF podem permitir o cruzamento alternativo.
Nome igual com dois CPFs integrais diferentes não permite a correspondência.
Homônimos que representem candidatos distintos exigem revisão da identidade;
a ambiguidade não interrompe a avaliação dos demais documentos.

## Revisões de fonte e o que o agente deve verificar

Uma nova importação pode mudar a evidência TSE sob a mesma chave de regra,
versão e documento. A verificação pode então falhar por conflito de
imutabilidade, conforme o
[ADR 0009](../../docs/adr/0009-immutable-finding-conflicts.md). Preservar achados
e revisões anteriores, sem reescrever silenciosamente a origem do candidato.

O agente deve conferir a natureza da divulgação, a emissão e o valor nos
documentos disponíveis, além do candidato e do método de correspondência
registrados. Deve citar a fonte TSE e apontar limitações de identidade ou de
cobertura sem inventar CPF, candidatura ou data. O agente anota o resultado
determinístico; a aprovação para o relatório final cabe à revisão humana.

## Registros contábeis e identidade (versão 2)

Um documento pode ocorrer em várias linhas da fonte. Manter um achado por
regra, versão e documento, preservando todas as ocorrências avaliadas em
`ocorrencias` e os arquivos e hashes em `registros_origem` quando houver
multiplicidade. O valor agregado soma os valores dessas ocorrências; não
representa automaticamente um valor recuperável. A proveniência principal
é uma âncora determinística ordenada por arquivo e hash do registro.

Correspondências ambíguas por nome e UF geram `revisar`, com todos os candidatos
e `correspondencia_ambigua = true`, sem afirmar a identidade do parlamentar.
