import streamlit as st
import os
from dotenv import load_dotenv
import logging
from database import load_or_create_chroma_db
from document_interface import DocumentInterface
from utils.retriever import query_rag_with_gpt, query_rag_with_cloud
from utils.formatter import format_response
from ui_components import apply_custom_css
from config import OPENAI_API_KEY, ANTHROPIC_API_KEY, DEFAULT_MODEL

class FinanceQAApp:
    def __init__(self, config_file='app_config.txt'):
        # Configura il logging per salvare tutte le domande e risposte
        logging.basicConfig(
            filename="chat_log.txt",
            level=logging.INFO,
            format="%(asctime)s - %(message)s"
        )

        # Carica le variabili di ambiente dal file `.env`
        load_dotenv()

        # Carica la configurazione dal file txt
        self.config = self.load_config(config_file)

        # Configura la pagina per usare tutta la larghezza disponibile e icona
        st.set_page_config(
            page_title=self.config.get('page_title', 'Finance Q&A'),
            layout="wide",
            page_icon=self.config.get('page_icon', '💬')
        )

        # Applica il CSS personalizzato
        apply_custom_css()

        # Inizializza il database e l'interfaccia documenti
        self.vector_store = load_or_create_chroma_db()
        self.doc_interface = DocumentInterface(self.vector_store)

        # Configura lo stato del toggle per previous_answer
        if "use_previous_answer" not in st.session_state:
            st.session_state["use_previous_answer"] = False  # Disabilitato di default

        # Configurazione dello stato della sessione
        if "history" not in st.session_state:
            st.session_state["history"] = []
        if "previous_answer" not in st.session_state:
            st.session_state["previous_answer"] = ""
        if "current_question" not in st.session_state:
            st.session_state["current_question"] = ""

        # Barra laterale con navigazione e cronologia
        st.sidebar.title(self.config.get('sidebar_navigation', '📚 Navigazione'))
        self.page = st.sidebar.radio("Vai a:", ["❓ Domande", "🗂️ Gestione Documenti"])

        st.sidebar.divider()

        # Selezione del modello di intelligenza artificiale
        st.sidebar.markdown("### Modello Intelligenza Artificiale")
        model_options = ["GPT (OpenAI)", "Claude (Anthropic)"]
        default_index = model_options.index(DEFAULT_MODEL) if DEFAULT_MODEL in model_options else 0
        self.model_choice = st.sidebar.selectbox("Seleziona il modello", model_options, index=default_index)

        # Verifica chiavi API
        if self.model_choice == "GPT (OpenAI)" and not OPENAI_API_KEY:
            st.sidebar.error("🔑 Chiave API OpenAI non impostata.")
        elif self.model_choice == "Claude (Anthropic)" and not ANTHROPIC_API_KEY:
            st.sidebar.error("🔑 Chiave API Anthropic non impostata.")

        # Toggle per abilitare o disabilitare l'uso del contesto della risposta precedente
        st.session_state["use_previous_answer"] = st.sidebar.checkbox(
            "Usa contesto della risposta precedente",
            value=st.session_state["use_previous_answer"],
            help="Attiva per includere la risposta precedente come contesto."
        )

        # Ripulisce il contesto precedente se il toggle è disabilitato
        if not st.session_state["use_previous_answer"]:
            st.session_state["previous_answer"] = ""

        st.sidebar.divider()
        # Mostra la cronologia nella barra laterale
        self.display_history()

    # Funzione per caricare la configurazione dal file txt
    def load_config(self, filename):
        config = {}
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    config[key.strip()] = value.strip()
        return config

    # Funzione per il logging delle interazioni
    def log_interaction(self, question, context, formatted_context, answer, history):
        """
        Registra i dettagli di ogni interazione nel file di log.
        """
        logging.info("Domanda: %s", question)
        logging.info("Contesto fornito: %s", context)
        logging.info("Contesto formattato per il modello: %s", formatted_context)
        logging.info("Risposta: %s", answer)
        logging.info("Cronologia: %s", history)

    # Funzione per aggiungere una domanda e risposta alla cronologia
    def add_to_history(self, question, answer, references):
        """
        Aggiunge una nuova domanda e risposta alla cronologia nella sessione.
        """
        # Elimina i duplicati nei riferimenti
        unique_references = {}
        for ref in references:
            file_name = ref["file_name"]
            file_path = ref["file_path"]
            if file_path not in unique_references:
                unique_references[file_path] = file_name

        references_text = "\n".join(
            [f"- [{name}](#{path})" for path, name in unique_references.items()]
        )

        history_entry = {
            "question": question,
            "answer": answer,
            "references": list(unique_references.items())  # Salva come lista di tuple (path, name)
        }
        st.session_state["history"].append(history_entry)

    # Funzione per visualizzare la cronologia
    def display_history(self):
        """
        Visualizza la cronologia nella barra laterale, in ordine inverso.
        Permette di selezionare una domanda con un menu a tre pallini.
        """
        st.sidebar.markdown(f"### {self.config.get('sidebar_history', '📜 Cronologia delle Domande')}")
        if st.session_state["history"]:  # Controlla che la cronologia non sia vuota
            for i, entry in enumerate(st.session_state["history"]):
                with st.sidebar.expander(f"❓ {entry['question']}", expanded=False):
                    st.markdown(f"**Risposta:** {entry['answer']}")
                    if entry["references"]:
                        st.markdown("**Riferimenti:**")
                        for file_path, file_name in entry["references"]:
                            st.markdown(f"- **{file_name}**", help=f"Percorso: {file_path}")
                    if st.button(f"Usa", key=f"history_button_{i}"):
                        st.session_state["current_question"] = entry["question"]
        else:
            st.sidebar.info("La cronologia è vuota.")

    def run(self):
        # Sezione Domande e Risposte
        if self.page == "❓ Domande":
            st.header(self.config.get('header_questions', "💬 Fai una Domanda"))

            col1, col2 = st.columns([3, 1])

            with col1:
                question = st.text_input(
                    self.config.get('default_question_placeholder', "📝 Inserisci la tua domanda:"),
                    max_chars=500,
                    help="Digita la tua domanda qui",
                    value=st.session_state.get("current_question", "")
                )

            with col2:
                expertise_level = st.selectbox(
                    "Livello competenza",
                    ["beginner", "intermediate", "expert"],
                    index=0,
                    help="Scegli il livello per adattare il dettaglio della risposta."
                )

            if self.vector_store and question:
                # Usa il contesto della risposta precedente solo se il toggle è abilitato
                if st.session_state["use_previous_answer"] and st.session_state["previous_answer"]:
                    question_with_context = (
                        f"{st.session_state['previous_answer']} \n\nDomanda attuale: {question}"
                    )
                else:
                    question_with_context = question

                # Esegui la query
                if self.model_choice == "GPT (OpenAI)" and OPENAI_API_KEY:
                    answer, references = query_rag_with_gpt(question_with_context, expertise_level=expertise_level)
                elif self.model_choice == "Claude (Anthropic)" and ANTHROPIC_API_KEY:
                    answer, references, _, _ = query_rag_with_cloud(question_with_context,
                                                                    expertise_level=expertise_level)
                else:
                    answer = "⚠️ La chiave API per il modello selezionato non è disponibile."
                    references = []

                # Mostra la risposta
                formatted_answer = format_response(answer, references)
                st.markdown(formatted_answer)

                # Aggiungi domanda, risposta e riferimenti alla cronologia
                self.add_to_history(question, answer, references)


                # Salva la cronologia nel file di log
                self.log_interaction(question, question_with_context, question_with_context, answer, st.session_state["history"])


                # Aggiorna l'ultima risposta se il toggle è abilitato
                if st.session_state["use_previous_answer"]:
                    st.session_state["previous_answer"] = answer

                # Ripulisci la domanda corrente
                st.session_state["current_question"] = ""
                st.divider()
            elif not self.vector_store:
                st.warning(
                    "🚨 Nessuna knowledge base disponibile. Carica un documento nella sezione 'Gestione Documenti'.")


        # Sezione Gestione Documenti
        elif self.page == "🗂️ Gestione Documenti":
            st.header(self.config.get('header_documents', "📁 Gestione Documenti"))
            st.markdown(self.config.get('default_document_message', "Carica, visualizza e gestisci i documenti nella knowledge base."))
            self.doc_interface.show()

    # Funzione principale per eseguire l'app
    @staticmethod
    def main():
        app = FinanceQAApp()
        app.run()

# Esegui l'applicazione
if __name__ == "__main__":
    FinanceQAApp.main()
