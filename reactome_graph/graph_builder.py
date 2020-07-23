import networkx as nx
import multiprocessing as mp
import neo4j
from reactome_graph.utils.neo4j import Neo4jClient
from typing import Sequence, Dict


EDGES_QUERY = 'reactome_graph/queries/edges.cypher'
PATHWAY_QUERY = 'reactome_graph/queries/pathways.cypher'


class GraphBuilder(object):
    """
    Reactome bipartite directed multigraph builder.

    Builds a graph for every species specified in the constructor.

    Parameters
    ----------
    species: list
        Sequence of species to extract from the Reactome database.

    relationships: list
        Sequence of relationships to extract from the Reactome database.
    """

    def __init__(self, species: Sequence[str] = None,
                 relationships: Sequence[str] = None):
        self._load_queries()
        self.relationships = relationships
        if species is None:
            self._load_species()
        else:
            self.species = species

    def _load_queries(self):
        with open(EDGES_QUERY, 'r') as f1, open(PATHWAY_QUERY, 'r') as f2:
            self._query_edges = f1.read()
            self._query_pathways = f2.read()

    def _load_species(self):
        client = Neo4jClient()
        result = client.make_query(
            'match (s:Species) return s.abbreviation as code;')
        self.species = [str(s['code']) for s in result]
        client.close()

    def _parse_records(self, edges: neo4j.Result,
                       pathways: neo4j.Result) -> nx.MultiDiGraph:

        nodes, edges = {}, []
        for record in edges:
            try:
                source = dict(record['source'])
                target = dict(record['target'])
                source['labels'] = list(record['sourceLabels'])
                target['labels'] = list(record['targetLabels'])
                rel_data = (dict(record['relData'])
                            if record['relData'] is not None
                            else {'order': None, 'stoichiometry': None})
                rel_data['type'] = str(record['relType'])
            except (ValueError, TypeError, KeyError):
                continue

            # filter out relationships
            if (self.relationships is not None
                    and rel_data['type'] not in self.relationships):
                continue

            # change edge direction
            if rel_data['type'] in ['input', 'catalyst', 'positiveRegulator',
                                    'negativeRegulator', 'catalystActiveUnit',
                                    'regulatorActiveUnit']:
                t = {**source}
                source = {**target}
                target = t

            # bipartite networkx convention
            source['bipartite'] = 1 if 'Event' in source['labels'] else 0
            target['bipartite'] = 1 if 'Event' in target['labels'] else 0

            nodes[source['stId']] = source
            nodes[target['stId']] = target
            edges.append((source['stId'], target['stId'], rel_data))

        for record in pathways:
            reaction, pathway = record['reaction'], record['pathway']
            if reaction not in nodes:
                continue
            if 'pathways' not in nodes[reaction]:
                nodes[reaction]['pathways'] = []
            nodes[reaction]['pathways'].append(pathway)

        graph = nx.MultiDiGraph()
        for edge in edges:
            source, target, rel_data = edge
            graph.add_node(source, **nodes[source])
            graph.add_node(target, **nodes[target])
            graph.add_edge(source, target, key=rel_data['type'], **rel_data)
        return graph

    def _extract_species(self, s: str) -> nx.MultiDiGraph:
        client = Neo4jClient()

        query_edges = self._query_edges.replace('$species', s)
        query_pathways = self._query_pathways.replace('$species', s)

        result_edges = client.make_query(query_edges)
        result_pathways = client.make_query(query_pathways)

        graph = self._parse_records(result_edges, result_pathways)

        client.close()
        return s, graph

    def _extract(self):
        pool = mp.Pool(mp.cpu_count())
        out = pool.map(self._extract_species, self.species)
        pool.close()
        pool.join()
        return out

    def build(self) -> Dict[str, nx.MultiDiGraph]:
        """
        Build `networkx.MultiDiGraph` for all species specified in constructor.
        Built graphs will also be stored in the species' data folder.

        Returns
        -------
        graphs: dict
            Dictionary containing graphs as values and species names as keys.
        """
        return {species: graph for species, graph in self._extract()}
