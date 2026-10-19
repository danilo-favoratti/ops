---
id: R14
version: "1"
severidade: media
fundamento: "O adicional não é cumulativo caso o parlamentar exerça mais de um desses cargos."
---

# R14 — Contas de liderança com adicional e saldo acumulado

## Fundamento e alcance

A seção de [adicionais da fonte](../../cota-parlamentar.md) apresenta valores
mensais por cargo e determina que o adicional não se acumula pelo exercício
simultâneo de vários cargos. O saldo financeiro não utilizado pode ser carregado
entre meses do mesmo exercício, conforme o fundamento da R07.

Esta versão é um controle aproximado, de severidade média, das contas de
liderança identificadas por `cpf` vazio e `siglaUF = "NA"`. Para essas contas,
a base de cota por UF é zero e a dotação é exatamente um adicional do cargo
reconhecido. Esse modelo não atribui uma UF nem presume uma segunda cota.

As políticas iniciais usam a data assumida `2023-01-01`, conforme o
[ADR 0008](../../docs/adr/0008-policy-effective-date-assumption.md). A identificação
da conta não reconstrói nomeações históricas ou todos os direitos de uma
liderança. O adicional por viagem do Mercosul não é um adicional mensal e não
integra esta regra.

## Campos e reconhecimento do cargo

Usar `despesas_current`, com `numero_deputado_id`, `ano`, `mes`, `valor_liquido`
e `id_documento`. Derivar a identidade dos registros da própria liderança em
cada ano, usando `nome_parlamentar`, `cpf` e `sigla_uf`. UF, classificação de
liderança ou cargo conflitante dentro do mesmo ano causa falha explícita.
O cargo é resolvido por mapeamento explícito de nomes conhecidos. Por exemplo,
`LIDERANÇA DO GOVERNO NA CÂMARA` corresponde a `lider_governo_camara`.

Consultar `adicionais` pelo cargo e selecionar, para cada mês, a maior
`valid_from` que não ultrapasse o primeiro dia da competência. Não somar cargos
nem escolher um adicional por aproximação textual. Nome sem cargo reconhecido
ou política vigente ausente causa falha registrada antes da publicação dos
achados da regra; não se usa zero como substituto silencioso.

## Lógica determinística

R14 parametriza o mesmo cálculo de saldo acumulado usado por R07. Para cada
liderança e ano, montar o calendário de janeiro até o último mês com despesas,
incluindo meses sem registros com gasto zero. A dotação acumulada é a soma de
um adicional mensal vigente por competência, com base por UF igual a zero.

Comparar o gasto líquido acumulado com a dotação acumulada multiplicada por
`1 + quota_margin_percent / 100`, sem arredondar o limiar. Emitir somente quando
o gasto for estritamente maior e houver registros de despesas no mês avaliado.
A igualdade não dispara. O documento associado é o maior `id_documento` do mês.
O disponível antes do mês é a dotação acumulada menos os gastos anteriores.
O calendário e o saldo reiniciam em janeiro de cada exercício.

A configuração `rules.quota_margin_percent` é compartilhada com R07: Decimal
finito e não negativo, padrão `5`, substituível por
`OPSIU_RULES__QUOTA_MARGIN_PERCENT`. A margem apenas filtra os achados. O valor
de cada achado é o gasto acumulado menos a dotação acumulada original, sem
descontar a tolerância. Excedentes de meses consecutivos se sobrepõem e não
devem ser somados para calcular um excesso único.

Alterar a margem modifica a evidência sem alterar a chave formada por regra,
versão e documento. Uma nova verificação sobre achados existentes pode falhar
por conflito de imutabilidade, conforme o
[ADR 0009](../../docs/adr/0009-immutable-finding-conflicts.md). Achados e revisões
anteriores devem ser preservados, sem sobrescrita silenciosa.

## Evidência

Registrar a identificação em `numero_deputado_id`, `ano`, `mes`, `uf`, `cargo`
e `lideranca`; os valores em `gasto_mensal`, `gasto_acumulado`,
`limite_acumulado`, `cota_mensal` e `adicional_mensal`; e a lista ordenada
`documentos` do mês. Nesta regra, a cota mensal por UF é zero.

Incluir `approximate = true`, `margem_percentual`, `limite_com_margem`,
`disponivel_no_mes` e `cargos_individuais_disponiveis = false`. O reconhecimento
do nome da conta não fornece o histórico dos cargos individuais. O disponível
do mês é calculado antes de descontar seu gasto corrente.

`serie_mensal` inclui meses sem gastos, com `mes`, `gasto`, `cota`, `adicional`,
`limite` e `vigente_desde` em cada elemento. O documento associado mantém a
proveniência de arquivo, registro, execução, etapa e versão da regra.

## Exemplos sintéticos

A conta `1001`, `LIDERANÇA DO GOVERNO NA CÂMARA`, tem CPF vazio e UF `NA`.
Seu adicional mensal do exemplo é R$ 1.353,04:

- Janeiro: gasto de R$ 1.500,00 supera o limiar exato de R$ 1.420,692 com
  margem de 5%. Gera achado de R$ 146,96 em `209900037`.
- Fevereiro: mais R$ 1.200,00 leva o gasto acumulado a R$ 2.700,00, dentro
  dos R$ 2.706,08 disponíveis em dois meses; não dispara.
- Março: mais R$ 1.353,04 leva o acumulado a R$ 4.053,04, dentro de três
  adicionais de R$ 4.059,12; não dispara.

O único documento positivo dessa conta na amostra é `209900037`. Os exemplos
são sintéticos e não demonstram o direito histórico de uma liderança real.

## O que o agente deve verificar

Confira se o nome da conta, o cargo reconhecido, as competências e a política
citada estão sustentados pela evidência disponível. Examine os documentos,
glosas e valores da série. Registre dúvidas sobre identidade, nomeações,
vigência histórica ou dotações da conta sem criar adicionais ou somar cargos.
Preserve a interpretação aproximada e a sobreposição dos excedentes mensais.

O agente produz anotações; o disparo da regra permanece determinístico.
A aprovação para o relatório final cabe à revisão humana.
