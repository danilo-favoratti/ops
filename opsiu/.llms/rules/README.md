# Documentos das regras

Cada regra registrada deve ter exatamente um arquivo `<id>-<nome>.md`, como
`R01-locacao-veiculos.md`. O identificador no nome deve coincidir com o campo
`id`. Arquivos de regras sem correspondência no registro e identificadores
duplicados são rejeitados. Este README não é um documento de regra.

O cabeçalho começa na primeira linha com `---` e termina com outra linha `---`.
São obrigatórios exatamente os campos `id`, `version`, `severidade` e
`fundamento`, todos não vazios. Exemplo apenas de formato, sem registrar uma
regra:

```text
---
id: R01
version: "1.0.0"
severidade: alta
fundamento: "Texto da fonte: trecho que fundamenta a verificação."
---

# Título da regra

Campos, lógica, exemplos e orientações para a anotação pelo agente.
```

O leitor aceita um formato limitado semelhante a YAML: um campo por linha,
sem recuo, com valor textual simples, entre aspas duplas no formato JSON ou entre
aspas simples. Dentro de aspas simples, use `''` para representar um apóstrofo.
Valores sem aspas são tratados como texto, inclusive versões numéricas. Apenas
o primeiro `:` separa o campo do valor; outros dois-pontos pertencem ao texto. Linhas
vazias e comentários que começam com `#` são aceitos entre os campos.

Listas, objetos, blocos multilinha (`|` e `>`), referências, marcadores de tipo e
comentários ao fim do valor não são aceitos. Para incluir ` #` no texto, coloque
o valor entre aspas. Campos adicionais e campos repetidos também são rejeitados.
O corpo Markdown após o cabeçalho não tem essas restrições.

`version`, `severidade` e `fundamento` devem coincidir com os valores do descritor
da regra no código. Alterações na lógica exigem nova versão e entrada no
`docs/CHANGELOG.md`. O registro vazio é válido sem documentos de regras.
