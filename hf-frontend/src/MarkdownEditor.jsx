import { useEditor, Milkdown, MilkdownProvider } from '@milkdown/react';
import { Crepe } from '@milkdown/crepe';
import { listener, listenerCtx } from '@milkdown/plugin-listener';

// Crepe 기본 테마 (Typora와 유사한 클린한 WYSIWYG 스타일)
import '@milkdown/crepe/theme/common/style.css';
import '@milkdown/crepe/theme/frame.css';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function MilkdownEditorInternal({ defaultValue, onChange }) {
    useEditor((root) => {
        const crepe = new Crepe({
            root,
            defaultValue,
            features: {
                [Crepe.Feature.ImageBlock]: true,
            },
            featureConfigs: {
                [Crepe.Feature.ImageBlock]: {
                    // 사용자가 이미지를 드래그하거나 붙여넣으면 호출됨.
                    // 데이터는 이 함수 안에서 우리 FastAPI 서버(로컬)로만 전송되며,
                    // Milkdown/Crepe 쪽 서버로는 어떤 데이터도 나가지 않는다.
                    onUpload: async (file) => {
                        const formData = new FormData();
                        formData.append('file', file);

                        const response = await fetch(`${API_URL}/upload/image`, {
                            method: 'POST',
                            body: formData,
                        });

                        if (!response.ok) {
                            throw new Error('이미지 업로드 실패');
                        }

                        const data = await response.json();
                        return data.url; // 이 URL이 마크다운에 ![](url) 형태로 자동 삽입됨
                    },
                },
            },
        });

        crepe.editor.use(listener).config((ctx) => {
            const listenerManager = ctx.get(listenerCtx);
            listenerManager.markdownUpdated((_, markdown, prevMarkdown) => {
                if (markdown !== prevMarkdown) {
                    onChange(markdown);
                }
            });
        });

        return crepe;
    }, []);

    return <Milkdown />;
}

// 주의: Crepe는 비제어(uncontrolled) 컴포넌트라 defaultValue는 최초 마운트 시 1회만 사용됨.
// 다른 기사로 전환할 때는 반드시 부모(ArticleCard)에서 key={article.id}를 넘겨서
// 컴포넌트를 완전히 새로 마운트해야 한다 (그래야 새 defaultValue가 반영됨).
export default function MarkdownEditor({ defaultValue, onChange }) {
    return (
        <MilkdownProvider>
            <MilkdownEditorInternal defaultValue={defaultValue} onChange={onChange} />
        </MilkdownProvider>
    );
}
